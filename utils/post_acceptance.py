from datetime import timedelta
import logging
import sqlite3
import time
from contextlib import closing
from typing import Dict, List, Optional

import botocore.client
import prefect
from prefect import task

from toloka_prefect.utils import with_logger, with_toloka_client
from toloka.client import Pool, Task as TolokaTask, TolokaClient
from toloka.streaming.cursor import AssignmentCursor
from crowdkit.aggregation import MajorityVote
import boto3
import botocore
import pandas as pd
import requests


from .answer import Answer
from .sql_templates import (
    DB,
    TEMPLATE_QUERY_MOVE_TO_PROCESSING,
    TEMPLATE_QUERY_GET_CONTENT,
    TEMPLATE_QUERY_SET_STATUSES,
)


@task
@with_logger
def db_move_into_processing(limit: Optional[int], *, logger: logging.Logger) -> None:
    flow_run_id = prefect.context['flow_run_id']
    with closing(sqlite3.connect(DB)) as conn:
        with closing(conn.cursor()) as cursor:
            sql_substitution_params = {
                'flow_run_id': flow_run_id,
                'limit': limit,
            }
            cursor.execute(TEMPLATE_QUERY_MOVE_TO_PROCESSING, sql_substitution_params)
            conn.commit()
    logger.info('Tasks are started processing')


@task
@with_logger
@with_toloka_client
def db_get_main_answers(client, *, logger: logging.Logger, toloka_client: TolokaClient) -> List[Answer]:
    flow_run_id = prefect.context['flow_run_id']
    with closing(sqlite3.connect(DB)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute(TEMPLATE_QUERY_GET_CONTENT, {'flow_run_id': flow_run_id})
            res_rows = cursor.fetchall()
            logger.info(f'СМОТРИ СЮДА!!!!!!!!!: {res_rows}')
            conn.commit()

    result = []
    s3 = _get_s3_client()
    basedir = 'attachments'
    bucket_name = 'prefect-example-fin'
    for task_id, assignment_id, user_id, video_attachment_id, photo_attachment_id, model in res_rows:
        video_url = _host_attachment(toloka_client, s3, basedir, bucket_name, video_attachment_id)
        photo_url = _host_attachment(toloka_client, s3, basedir, bucket_name, photo_attachment_id)

        ans = Answer(
            task_id=task_id,
            assignment_id=assignment_id,
            user_id=user_id,
            output_values={
                'video_url': video_url,
                'photo_url': photo_url,
                'phone_': model
            },
        )
        result.append(ans)
    logger.info(f'New answers in process: {len(result)}')
    return result


def _get_s3_client(endpoint_url: str = 'https://storage.yandexcloud.net'):
    secret = client.Secret('AWS_CREDENTIALS').get()
    session = boto3.session.Session(
        aws_access_key_id=secret['ACCESS_KEY'],
        aws_secret_access_key=secret['SECRET_ACCESS_KEY'],
    )
    return session.client(
        service_name='s3',
        endpoint_url=endpoint_url,
    )


def _host_attachment(toloka_client: TolokaClient,s3: botocore.client.BaseClient,basedir: str,bucket_name: str,attachment_id: str,) -> str:
    attachment = toloka_client.get_attachment(attachment_id)
    filepath = f'{basedir}/{attachment.name}'
    with open(filepath, 'wb') as att_out:
        toloka_client.download_attachment(attachment_id, att_out)
    s3.upload_file(f'{basedir}/{attachment.name}', bucket_name, attachment.name)
    return f'{s3.meta.endpoint_url}/{bucket_name}/{attachment.name}'


@task
@with_toloka_client
@with_logger
def create_val_tasks(answers: List[Answer],val_pool_id: str,*,logger: logging.Logger) -> List[TolokaTask]:
    tasks_prepared = [
        TolokaTask(
            pool_id=val_pool_id,
            unavailable_for=[answer.user_id],
            input_values={
                'videooo': answer.output_values['video_url'],
                'selfie': answer.output_values['photo_url'],
                'phoone': answer.output_values['phone_'],
                'assignment_id': answer.assignment_id,
            })
        for answer in answers
    ]
    res = toloka_client.create_tasks(tasks_prepared, allow_defaults=True, open_pool=True)
    logger.info(f'Created validation tasks count: {len(res.items)}')
    return list(res.items.values())


@task
@with_toloka_client
@with_logger
def get_answers(tasks: List[TolokaTask],*,period: timedelta = timedelta(minutes=1),overlap: Optional[int] = None,logger: logging.Logger,toloka_client: TolokaClient,) -> List[Answer]:
    pool_id = tasks[0].pool_id
    start_time = min(task.created for task in tasks)
    cursor_kwargs = {'pool_id': pool_id, 'created_gte': start_time, 'toloka_client': toloka_client}
    it_submitted = AssignmentCursor(event_type='SUBMITTED', **cursor_kwargs)
    logger.info(f'Start from: {start_time}')

    remaining_by_task: Dict[str, int] = {task.id: overlap or task.overlap for task in tasks}
    result = []
    while True:
        new_answers = _get_new_answers(it_submitted, remaining_by_task)
        result.extend(new_answers)
        logger.info('New answers submitted count: %d. Total count: %d', len(new_answers), len(result))

        finished_completely_count = sum(1 for remaining in remaining_by_task.values() if not remaining)
        logger.info(f'Finished tasks count: {finished_completely_count}')
        logger.debug(f'Remaining answers count by task: {remaining_by_task}')

        if finished_completely_count == len(remaining_by_task):
            return result
        elif not new_answers:
            pool = toloka_client.get_pool(pool_id)
            if pool.status != Pool.Status.OPEN:
                raise ValueError(f'Waiting for pool {pool_id} in status {pool.status.value}')
        logger.info(f'Sleep for {period.total_seconds()} seconds')
        time.sleep(period.total_seconds())


def get_sets_in_validation(raw_val_pool_id):
    r = requests.get(f'https://toloka.dev/api/v1/tasks?pool_id={raw_val_pool_id}',headers=HEADERS).json()
    print(raw_val_pool_id)
    print(r)
    answers = r['items']
    validated_sets = [
        TolokaTask(
            pool_id=raw_val_pool_id,
            # unavailable_for=[answer.user_id],
            input_values={
                'video_url': answer['input_values']['video_url'],
                'photo_url': answer['input_values']['photo_url'],
                'phone_': answer['input_values']['phone_'],
                'assignment_id': answer['input_values']['assignment_id'],
            })
        # for answer in answers if not answer.assignment_id in
        for answer in answers
    ]
    return validated_sets



def _get_new_answers(it_submitted, remaining_by_task: Dict[str, int]) -> List[Answer]:
    new_answers: List[Answer] = []
    for event in it_submitted:
        for _task, solution in zip(event.assignment.tasks, event.assignment.solutions):
            if _task.id in remaining_by_task:
                remaining_by_task[_task.id] = max(remaining_by_task[_task.id] - 1, 0)
                new_answers.append(Answer(_task.id,
                                          event.assignment.id,
                                          _task.input_values,
                                          event.assignment.user_id,
                                          solution.output_values))
    return new_answers


@task
def aggregate(val_answers: List[Answer]) -> Dict[str, str]:
    tuples = [
        (answer.input_values['assignment_id'], answer.output_values['is_ok'], answer.user_id)
        for answer in val_answers
    ]
    df = pd.DataFrame(tuples, columns=['task', 'label', 'worker'])
    return MajorityVote().fit_predict(df).to_dict()


@task
def select_accepted(aggregated: Dict[str, str]) -> List[str]:
    to_accept = [assignment_id for assignment_id, is_ok in aggregated.items() if is_ok]
    for set in to_accept:
        toloka_client.accept_assignment(assignment_id=set, public_comment='Well done')

    return to_accept


@task
def select_rejected(aggregated: Dict[str, str]) -> List[str]:
    to_reject = [assignment_id for assignment_id, is_ok in aggregated.items() if not is_ok]

    for set in to_reject:
        toloka_client.reject_assignment(assignment_id=set, public_comment='Incorrect object')

    return to_reject


@task
@with_logger
def db_save_results(accepted: List[str], rejected: List[str], *, logger: logging.Logger) -> None:
    data = []
    for accepted_assignment in accepted:
        data.append((accepted_assignment, 'ACCEPTED'))
    number_of_accepted_assignments = len(data)
    for rejected_assignment in rejected:
        data.append((rejected_assignment, 'REJECTED'))
    logger.info(f'Accepted assignments: {number_of_accepted_assignments}. '
                f'Rejected assignments: {len(data) - number_of_accepted_assignments}')
    with closing(sqlite3.connect(DB)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.executemany(TEMPLATE_QUERY_SET_STATUSES, data)
            conn.commit()
