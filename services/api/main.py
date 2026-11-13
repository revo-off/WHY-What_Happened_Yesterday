import os

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import create_engine, text

app = FastAPI(title="WHY - What Happened Yesterday")
Instrumentator().instrument(app).expose(app)
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


@app.get("/health")
def health():
    with engine.connect() as c:
        c.execute(text("select 1"))
    return {"status": "ok"}
