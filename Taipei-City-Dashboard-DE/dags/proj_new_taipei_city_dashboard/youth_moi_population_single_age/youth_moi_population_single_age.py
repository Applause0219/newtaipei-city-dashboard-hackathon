import requests

from operators.common_pipeline import CommonDag
from utils.get_time import get_tpe_now_time_str
from utils.youth_p0_sources import (
    build_moi_single_age_data,
    fetch_bytes,
    fetch_data_gov_resource,
)


DATASET_ID = 14226
FALLBACK_RESOURCE_URL = (
    "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/"
    "444B4051-AD04-4874-9CFF-CD8D12EA2D0A/resource/"
    "A4C4FC3A-8C07-4928-8D20-AAA129194312/download"
)


def fetch_records():
    with requests.Session() as session:
        url = fetch_data_gov_resource(
            session,
            DATASET_ID,
            fallback_url=FALLBACK_RESOURCE_URL,
        )
        payload = fetch_bytes(session, url, timeout=180)
    return payload, url


def transform_records(payload, source_url, data_time=None):
    return build_moi_single_age_data(
        payload,
        dataset_id=str(DATASET_ID),
        source_url=source_url,
        data_time=data_time,
    )


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    payload, source_url = fetch_records()
    data = transform_records(
        payload,
        source_url,
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"MOI population single age ready_data shape =========== {data.shape}")
    engine = create_engine(kwargs["ready_data_db_uri"])
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos["load_behavior"],
        default_table=dag_infos["ready_data_default_table"],
    )
    update_lasttime_in_data_to_dataset_info(
        engine,
        dag_infos["dag_id"],
        data["data_time"].max(),
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_moi_population_single_age",
)
dag.create_dag(etl_func=_transfer)
