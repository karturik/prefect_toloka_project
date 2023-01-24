import datetime
import logging
import sqlite3
from contextlib import closing
from typing import List, Tuple

import prefect
from prefect import task
from prefect.tasks.secrets import PrefectSecret
from toloka.client import Task as TolokaTask, TolokaClient
from toloka_prefect.utils import with_logger, with_toloka_client

import requests
import json

from .answer import Answer
from .sql_templates import (
    DB,
    TEMPLATE_QUERY_START_PROCESS,
    TEMPLATE_GET_CREATED_TS,
    TEMPLATE_GET_TWO_LAST_PROCESSES,
    TEMPLATE_QUERY_MOVE_TO_WAITING,
)

@task
@with_logger
@with_toloka_client
def create_pool(client, pool_json:json, logger: logging.Logger) -> str:
    project_id = pool_json['project_id']
    logger.info(f'Creating pool for project {project_id}')
    pool_name = pool_json['private_name']
    pool_id = 0
    pool_aready_exixts = False
    for status in ['CLOSED', 'OPEN']:
        r = requests.get(f'https://toloka.yandex.ru/api/v1/pools?project_id={project_id}&status={status}', headers=HEADERS).json()
        # print(r)
        for pool_data in r['items']:
            if pool_name == pool_data['private_name']:
                pool_aready_exixts = True
                pool_id = pool_data['id']

    if not pool_aready_exixts:
        r = requests.post('https://toloka.yandex.ru/api/v1/pools', json=pool_json, headers=HEADERS).json()
        # print(r)
        pool_id = r['id']
    else:
        logger.info(f'Pool already exists, id: {pool_id}')

    if pool_id != 0:
        return str(pool_id)


@task
@with_logger
@with_toloka_client
def get_tasks_count(pool_id: str, *, logger: logging.Logger) -> int:
    tasks = toloka_client.get_tasks(pool_id=pool_id)
    result = len(list(tasks))
    logger.info(f'There are {result} tasks in pool now')
    return result


@task
@with_logger
@with_toloka_client
def create_tasks(pool_id: str,tasks_count: int,*,toloka_client: TolokaClient,logger: logging.Logger,) -> List[TolokaTask]:
    logger.info(f'{tasks_count} tasks need to be created')
    tasks_prepared = [
        TolokaTask(
            input_values={'img_url': 'https://yastatic.net/s3/toloka/p/toloka-requester-page-project-preview/877a55555a5f199dff16.jpg'},
            pool_id=pool_id
        ) for _ in range(tasks_count)]
    res = toloka_client.create_tasks(tasks_prepared, allow_defaults=True, open_pool=True)
    created_tasks = list(res.items.values())
    logger.info(f'Tasks created: {len(created_tasks)}')
    return created_tasks


@task
@with_logger
def db_start_process_and_get_last_interval(*, logger: logging.Logger) -> Tuple[datetime.datetime, datetime.datetime]:
    flow_run_id = prefect.context['flow_run_id']
    with closing(sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES |
                                                  sqlite3.PARSE_COLNAMES)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute(TEMPLATE_QUERY_START_PROCESS, (flow_run_id,))

            cursor.execute(TEMPLATE_GET_CREATED_TS, (flow_run_id,))
            created_ts = cursor.fetchone()[0]

            cursor.execute(TEMPLATE_GET_TWO_LAST_PROCESSES, {'flow_run_id': flow_run_id, 'ts': created_ts})
            last_timestamps = cursor.fetchall()
            last_timestamps = [ts[0] for ts in last_timestamps]
            conn.commit()
    if len(last_timestamps) == 1:
        result = (None, last_timestamps[0])
    else:
        result = (last_timestamps[1], last_timestamps[0])
    logger.info(f'Time interval for processing: {result}')
    return result


@task
@with_logger
# @with_toloka_client
def get_answers_in_interval(pool_id: str, interval: Tuple[datetime.datetime, datetime.datetime], *,logger: logging.Logger) -> List[Answer]:
    logger.info(f'pool_id: {pool_id}')
    # assignments = toloka_client.get_assignments(status='SUBMITTED', pool_id=pool_id,submitted_gte=interval[0], submitted_lt=interval[1])
    assignments = toloka_client.get_assignments(status='SUBMITTED', pool_id=pool_id)
    result = []
    for assignment in assignments:
        for task, solution in zip(assignment.tasks, assignment.solutions):
            if solution is None:
                continue
            result.append(Answer(
                task_id=task.id,
                assignment_id=assignment.id,
                user_id=assignment.user_id,
                output_values=solution.output_values,
            ))
    logger.info(f'Found new answers in interval: {len(result)}')
    return result


@task
@with_logger
def db_save_answers(answers: List[Answer], *, logger: logging.Logger) -> None:
    query = TEMPLATE_QUERY_MOVE_TO_WAITING

    data = [
        (
            answer.task_id,
            answer.assignment_id,
            answer.user_id,
            answer.output_values['result'],
            answer.output_values['photo'],
            answer.output_values['phone'],
        )
        for answer in answers
    ]
    with closing(sqlite3.connect(DB)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.executemany(query, data)
            conn.commit()
    logger.info(f'Saved new answers: {len(data)}')
