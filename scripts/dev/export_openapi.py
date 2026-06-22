import json

from app.main import app


def export_openapi():
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, indent=2)


if __name__ == "__main__":
    export_openapi()
