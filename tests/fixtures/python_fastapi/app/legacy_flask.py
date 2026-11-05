from flask import Blueprint, Flask

bp = Blueprint("legacy", __name__, url_prefix="/legacy")


@bp.route("/products", methods=["GET", "POST"])
def legacy_products():
    return []


class ReportService:
    def build(self) -> dict:
        return {}
