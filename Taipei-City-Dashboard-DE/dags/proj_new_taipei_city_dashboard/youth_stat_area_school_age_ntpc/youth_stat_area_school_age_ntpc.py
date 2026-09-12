import requests

from operators.common_pipeline import CommonDag
from utils.get_time import get_tpe_now_time_str
from utils.youth_p0_sources import (
    build_stat_area_school_age_data,
    fetch_bytes,
    fetch_data_gov_resource,
    parse_stat_xml,
)


DATASET_ID = 18680
FALLBACK_RESOURCE_URL = (
    "https://segisws.moi.gov.tw/STATWSSTData/OpenService.asmx/"
    "GetStatSTDataForOpenCode?oCode="
    "6E03CA29B955A854D8F52522E38D8C7051A1FB52F41FB9881F3E9A2752D3D7B6772E860956DEC9A8D69E14E03D116C7D8EACF3"
)


def fetch_records():
    with requests.Session() as session:
        url = fetch_data_gov_resource(
            session,
            DATASET_ID,
            description_contains="新北市統計區單歲年齡組學齡人口統計_二級發布區",
            fallback_url=FALLBACK_RESOURCE_URL,
        )
        columns, rows, _ = parse_stat_xml(fetch_bytes(session, url))
    return columns, rows, url


def transform_records(columns, rows, source_url, data_time=None):
    return build_stat_area_school_age_data(
        columns,
        rows,
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
    columns, rows, source_url = fetch_records()
    data = transform_records(
        columns,
        rows,
        source_url,
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"stat area school age ready_data shape =========== {data.shape}")
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
    dag_folder="youth_stat_area_school_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
