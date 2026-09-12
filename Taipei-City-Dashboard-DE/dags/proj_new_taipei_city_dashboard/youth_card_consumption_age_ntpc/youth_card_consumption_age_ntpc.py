from operators.common_pipeline import CommonDag
from utils.get_time import get_tpe_now_time_str
from utils.youth_p0_sources import UA, build_card_consumption_data, fetch_bytes


SOURCE_URL = "https://www.nccc.com.tw/dataDownload/Age%20Group/BANK_MCT_ALL_AG.CSV"


def fetch_records():
    import requests

    with requests.Session() as session:
        return fetch_bytes(session, SOURCE_URL, timeout=180)


def transform_records(payload, data_time=None):
    return build_card_consumption_data(
        payload,
        source_url=SOURCE_URL,
        data_time=data_time,
    )


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    data = transform_records(
        fetch_records(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"card consumption ready_data shape =========== {data.shape}")
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
    dag_folder="youth_card_consumption_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
