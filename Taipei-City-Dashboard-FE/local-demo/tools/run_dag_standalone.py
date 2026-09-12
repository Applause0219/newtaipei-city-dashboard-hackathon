#!/usr/bin/env python3
"""不啟動 Airflow，直接跑 DE 的 ETL DAG。

為什麼需要這支：
    71 支 youth DAG 的抽取與轉換邏輯是純 Python（requests / pandas），
    只有最後兩行 `CommonDag(...)` / `create_dag()` 需要 Airflow。
    為了灌一次資料而架整套 Airflow（排程器、metadata DB、worker）
    並不划算，而且公司網路擋 Docker。

    這支用假的 airflow 模組讓 DAG 檔能 import 成功，然後直接呼叫
    每支 DAG 都有的統一入口 `_transfer(**kwargs)`。

放在 local-demo/tools/ 而不是 DE/ 底下：
    組員正在重構 DE，這支是我這條分支的工具，放在自己的地盤避免衝突。

用法：
    python3 run_dag_standalone.py --list
    python3 run_dag_standalone.py youth_housing_burden_ntpc
    python3 run_dag_standalone.py --all --limit 5
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

# 政府網站多用 TWCA Global Root CA，而 certifi 的套件庫沒有收錄它，
# 於是 requests 會報 CERTIFICATE_VERIFY_FAILED，但 curl／openssl 沒事
# （它們讀的是 macOS 系統信任庫）。
#
# truststore 讓 Python 改用系統信任庫。這是修正信任來源，不是關閉驗證——
# 千萬不要改成 verify=False，那會讓中間人攻擊變成可能。
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    print("提醒：沒有 truststore，政府網站可能報 CERTIFICATE_VERIFY_FAILED",
          file=sys.stderr)

# ── 專案路徑 ──
HERE = Path(__file__).resolve()
REPO = HERE.parents[3]                      # …/Taipei-City-Dashboard
DE = REPO / "Taipei-City-Dashboard-DE"
DAGS = DE / "dags"
PROJ = DAGS / "proj_new_taipei_city_dashboard"

DB_URI = os.environ.get(
    "READY_DATA_DB_URI", "postgresql://localhost:5432/dashboard"
)


def install_airflow_stubs():
    """讓 `from airflow import DAG` 這類 import 不會炸。

    DAG 檔在 module 層級就 import airflow，所以必須在 import 前先放假模組。
    這些 stub 只要能被 import、能被呼叫就好——真正的 ETL 邏輯不碰它們。
    """
    def stub(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class _Any:
        """吃掉任何呼叫與屬性存取的萬用替身。"""
        def __init__(self, *a, **k): pass
        def __call__(self, *a, **k): return self
        def __getattr__(self, _): return _Any()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    stub("airflow", DAG=_Any, __version__="stub")
    stub("airflow.models", Variable=_Any())
    stub("airflow.operators", )
    stub("airflow.operators.empty", EmptyOperator=_Any)
    stub("airflow.operators.python", PythonOperator=_Any)
    stub("airflow.providers", )
    stub("airflow.providers.postgres", )
    stub("airflow.providers.postgres.hooks", )
    stub("airflow.providers.postgres.hooks.postgres", PostgresHook=_Any)

    # settings.global_config 會讀 airflow.configuration，改成寫死的值
    stub(
        "settings.global_config",
        DAG_PATH=str(DAGS),
        DATA_PATH=str(DE / "data"),
        PROXIES=None,
        RAW_DATA_DB_URI=DB_URI,
        READY_DATA_DB_URI=DB_URI,
    )
    # operators.common_pipeline 只在 DAG 檔尾端用來註冊排程，做成 no-op
    stub("operators", )
    stub("operators.common_pipeline", CommonDag=_Any)

    # geopandas 在 utils/ 是 module 層級 import，但 youth 這批全是表格資料，
    # 實際只用在 `isinstance(data, gpd.GeoDataFrame)` 這個判斷上。
    # 在 Python 3.14 裝 geopandas 需要編 GDAL/GEOS，為了一個 isinstance 不值得。
    # 放一個沒有任何東西會是它的 instance 的空類別即可。
    try:
        import geopandas  # noqa: F401  真的裝了就用真的
    except ImportError:
        class _NeverMatches:
            """不會有任何物件是它的 instance，所以 geo 分支永遠不會被走到。"""

        stub("geopandas", GeoDataFrame=_NeverMatches, GeoSeries=_NeverMatches,
             read_file=_Any(), points_from_xy=_Any())
        stub("shapely", )
        stub("shapely.geometry", Point=_Any, shape=_Any())
        stub("shapely.wkt", loads=_Any())


def patch_sqlalchemy_autocommit():
    """讓 DAG 內建的 create_engine 取得 AUTOCOMMIT 連線。

    為什麼需要：
        utils/load_stage.py 寫的是 SQLAlchemy 1.4 的用法——
        `conn = engine.connect()` 之後直接 to_sql，靠 `autocommit=True`
        執行選項落地，全程沒有 commit()。

        SQLAlchemy 2.0 移除了那個執行選項，連線關閉時整批變更就被回滾。
        症狀非常難查：DAG 印出「Data been saved」、shape 也對，
        但資料表是空的，完全沒有錯誤訊息。

        pandas 2.3 又已經不支援 SQLAlchemy 1.4（會警告
        "Other DBAPI2 objects are not tested" 然後拿不到 cursor），
        所以不能靠降版解決。

    這裡用 isolation_level="AUTOCOMMIT" 補回原本的語意，
    不改 DE 的任何一行程式碼——那邊組員正在重構。
    """
    import sqlalchemy

    original = sqlalchemy.create_engine

    def create_engine_autocommit(*args, **kwargs):
        kwargs.setdefault("isolation_level", "AUTOCOMMIT")
        return original(*args, **kwargs)

    sqlalchemy.create_engine = create_engine_autocommit


def load_dag_module(dag_id: str):
    """把單一 DAG 檔 import 成模組。"""
    path = PROJ / dag_id / f"{dag_id}.py"
    if not path.exists():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(f"_dag_{dag_id}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_one(dag_id: str, timeout_note=""):
    """跑一支 DAG，回傳 (ok, 訊息, 耗時秒)。"""
    t0 = time.time()
    cfg_path = PROJ / dag_id / "job_config.json"
    if not cfg_path.exists():
        return False, "沒有 job_config.json", 0.0
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    dag_infos = cfg.get("dag_infos", {})

    try:
        mod = load_dag_module(dag_id)
    except Exception as e:
        return False, f"import 失敗：{type(e).__name__}: {e}", time.time() - t0

    transfer = getattr(mod, "_transfer", None)
    if transfer is None:
        return False, "沒有 _transfer()", time.time() - t0

    try:
        transfer(ready_data_db_uri=DB_URI, dag_infos=dag_infos)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        return False, msg.replace("\n", " ")[:220], time.time() - t0

    return True, "", time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dag_ids", nargs="*", help="要跑的 DAG id")
    ap.add_argument("--list", action="store_true", help="只列出所有 DAG")
    ap.add_argument("--all", action="store_true", help="跑全部")
    ap.add_argument("--limit", type=int, default=0, help="--all 時最多跑幾支")
    args = ap.parse_args()

    all_ids = sorted(p.name for p in PROJ.iterdir()
                     if p.is_dir() and p.name.startswith("youth"))

    if args.list:
        print(f"共 {len(all_ids)} 支：")
        for d in all_ids:
            print("  " + d)
        return

    targets = args.dag_ids or (all_ids if args.all else [])
    if not targets:
        ap.error("要指定 DAG id，或用 --all")
    if args.limit:
        targets = targets[: args.limit]

    install_airflow_stubs()
    patch_sqlalchemy_autocommit()
    sys.path.insert(0, str(DAGS))

    ok, fail = [], []
    for i, dag_id in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {dag_id} … ", end="", flush=True)
        good, msg, secs = run_one(dag_id)
        if good:
            ok.append(dag_id)
            print(f"✔ {secs:.1f}s")
        else:
            fail.append((dag_id, msg))
            print(f"✗ {secs:.1f}s  {msg}")

    print(f"\n成功 {len(ok)} / 失敗 {len(fail)}")
    if fail:
        print("\n失敗清單：")
        for d, m in fail:
            print(f"  {d}\n      {m}")


if __name__ == "__main__":
    main()
