import requests

from operators.common_pipeline import CommonDag
from utils.get_time import get_tpe_now_time_str
from utils.youth_p0_sources import (
    build_moi_single_age_data,
    fetch_bytes,
    fetch_data_gov_resource,
)


DATASET_ID = 9606
FALLBACK_RESOURCE_URL = (
    "https://statis.moi.gov.tw/micst/webMain.aspx?sys=220&kind=21&type=1"
    "&funid=c0110503&cycle=41&outmode=12&utf=1&compmode=0&outkind=3"
    "&fldspc=0,102,&codspc0=0,2,3,2,6,1,9,1,12,1,15,16,&codlst1=111"
    "&rdm=p4aAid9l&ym=11112&ymt=11112"
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
    print(f"MOI indigenous single age ready_data shape =========== {data.shape}")
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
    dag_folder="youth_moi_indigenous_single_age",
)
dag.create_dag(etl_func=_transfer)
