from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from src.business.profile import PROFILE
from src.config.settings import ARTIFACTS, Session
from src.constants.workflow import Decision
from src.services import reviews, runs, shifts
from src.utils.csv_data import render_csv


def result_keys():
    return [column["key"] for column in PROFILE["result_columns"]]


def download(run_id):
    with Session() as db:
        run = runs.require(db, run_id)
        if not run.artifact:
            raise HTTPException(409, "计算完成后才能导出。")
        path = ARTIFACTS / run.artifact
        if not path.is_file():
            raise HTTPException(409, "找不到导出产物，请创建新的计算单。")
        return FileResponse(path, media_type="text/csv; charset=utf-8", filename=f"{run_id}-analysis.csv")


def signed_download(run_id):
    with Session() as db:
        run = runs.require(db, run_id)
        review = reviews.lookup(db, run_id)
        if review is None or review.decision != Decision.APPROVE.value:
            raise HTTPException(409, "复核员签发后才能下载交接单。")
        reservation = shifts.reservation_for(db, run_id)
        # The handoff sheet carries only the samples this shift actually planned to print.
        booked = set(reservation.samples or []) if reservation is not None else set()
        selected = set(review.samples) & booked
        if not selected:
            raise HTTPException(409, "签发样本都不在本班排班内，没有可交接的纸样。")
        rows = [
            {**row, "reviewer": review.reviewer, "review_note": review.note}
            for row in run.result["rows"] if row["sample_id"] in selected
        ]
        contents = render_csv(rows, result_keys() + ["reviewer", "review_note"], protect=True)
        return Response(
            "\ufeff" + contents,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{run_id}-handoff.csv"'},
        )
