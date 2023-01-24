from prefect import Flow, Parameter, unmapped, Client
from toloka_prefect import accept_assignment, reject_assignment
from prefect.tasks.control_flow.case import case
from prefect.tasks.core.operators import LessThan, NotEqual, Equal
from toloka.client import TolokaClient
import json

client = Client()

client.set_secret(name="TOLOKA_TOKEN", value="")
client.set_secret(name="AWS_CREDENTIALS", value={"ACCESS_KEY": "", "SECRET_ACCESS_KEY": ""})

from utils import (
    db_start_process_and_get_last_interval,
    create_pool,
    get_tasks_count,
    create_tasks,
    get_answers_in_interval,
    db_save_answers,
    db_move_into_processing,
    db_get_main_answers,
    create_val_tasks,
    get_answers,
    aggregate,
    select_accepted,
    select_rejected,
    db_save_results,
    get_sets_in_validation
)

MESSAGE_ACCEPT = 'Well done'
MESSAGE_REJECT = 'Incorrect object'

pool_json = json.load(open('./toloka_configs/pool.json', encoding='utf-8'))
validation_pool_json = json.load(open('./toloka_configs/validation_pool.json', encoding='utf-8'))
project_json = json.load(open('./toloka_configs/project.json', encoding='utf-8'))
validation_project_json = json.load(open('./toloka_configs/validation_project.json', encoding='utf-8'))

with Flow('main-flow') as flow:

    OAUTH_TOKEN = ''
    toloka_client = TolokaClient(OAUTH_TOKEN, 'PRODUCTION')

    raw_pool_id = create_pool(client, pool_json)
    raw_val_pool_id = create_pool(client, validation_pool_json)

    pool_id = Parameter('pool_id', str(raw_pool_id))
    needed_count = Parameter('total_markup_count', 65)
    val_pool_id = Parameter('val_pool_id', str(raw_val_pool_id))

    interval_for_processing = db_start_process_and_get_last_interval()
    tasks_count = get_tasks_count(raw_pool_id, upstream_tasks=[interval_for_processing])
    with case(LessThan()(tasks_count, needed_count), True):
        create_tasks(raw_pool_id, needed_count - tasks_count)

    new_answers = get_answers_in_interval(raw_pool_id, interval_for_processing)
    #на этом моменте сеты сохраняются в БД со статусом WAITING
    _saved_answers = db_save_answers(new_answers)

    _moved = db_move_into_processing(limit=50, upstream_tasks=[_saved_answers])
    content_from_tasks = db_get_main_answers(upstream_tasks=[_moved])

    with case(NotEqual()(content_from_tasks, []), True):
        val_tasks = create_val_tasks(content_from_tasks, raw_val_pool_id)
        val_answers = get_answers(val_tasks)
        aggregated = aggregate(val_answers)
        to_accept = select_accepted(aggregated)
        to_reject = select_rejected(aggregated)
        db_save_results(to_accept, to_reject)

# flow.run()
flow.register(project_name="first_toloka_project")

