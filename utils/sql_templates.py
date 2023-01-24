"""
SQL templates and queries for processing flow runs and tasks
"""
DB = './tmp/prefect_example.db'


INIT_QUERY = '''
DROP TABLE IF EXISTS processes;
CREATE TABLE IF NOT EXISTS processes (
    flow_run_id     CHAR(36) PRIMARY KEY,
    created_ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX processes_index
ON processes(created_ts, flow_run_id);

DROP TABLE IF EXISTS content;
CREATE TABLE IF NOT EXISTS content (
    task_id         TEXT,
    assignment_id   TEXT PRIMARY KEY,
    user_id         TEXT,
    video_url       TEXT,
    photo_url       TEXT,
    phone           TEXT,
    status          TEXT DEFAULT "WAITING",
    flow_run_id     CHAR(36) NULL
);
'''

TEMPLATE_QUERY_START_PROCESS = '''
    INSERT INTO processes (flow_run_id)
    VALUES (?)
'''

TEMPLATE_GET_CREATED_TS = '''
    SELECT created_ts FROM processes WHERE flow_run_id = ? 
'''

TEMPLATE_GET_TWO_LAST_PROCESSES = '''
    SELECT created_ts FROM processes
    WHERE
        flow_run_id = :flow_run_id OR
        created_ts < :ts OR 
        created_ts = :ts AND flow_run_id < :flow_run_id
    ORDER BY created_ts DESC LIMIT 2
'''

TEMPLATE_QUERY_MOVE_TO_WAITING = '''
    INSERT INTO content (task_id, assignment_id, user_id, video_url, photo_url, phone, status)
    VALUES
        (?, ?, ?, ?, ?, ?, "WAITING")
'''

TEMPLATE_QUERY_MOVE_TO_PROCESSING = '''
    UPDATE content 
    SET 
        flow_run_id = :flow_run_id,
        status = "IN_PROCESS"
    WHERE assignment_id IN (
        SELECT assignment_id FROM content WHERE status = "WAITING"
        LIMIT :limit
    )
'''

# TEMPLATE_QUERY_GET_CONTENT = '''
#     SELECT task_id, assignment_id, user_id, video_url, photo_url, phone
#     FROM content
#     WHERE flow_run_id = :flow_run_id
# '''

TEMPLATE_QUERY_GET_CONTENT = '''
    SELECT task_id, assignment_id, user_id, video_url, photo_url, phone
    FROM content
    WHERE status = 'IN_PROCESS'
'''

TEMPLATE_QUERY_SET_STATUSES = '''
    INSERT INTO content (assignment_id, status)
    VALUES (?, ?)
    ON CONFLICT (assignment_id) DO
    UPDATE SET
        status = excluded.status
'''
