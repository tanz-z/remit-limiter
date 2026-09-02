from fastapi import FastAPI
from app.middleware import RateLimiterMiddleware
from app.redis_client import close_redis

app = FastAPI(title="Distributed Rate Limiter Demo")
app.add_middleware(RateLimiterMiddleware)


@app.on_event("shutdown")
async def shutdown():
    await close_redis()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/resource")
async def get_resource():
    """A stand-in for whatever endpoint you actually want protected."""
    return {"data": "here's your resource"}


@app.get("/api/heavy")
async def heavy_resource():
    return {"data": "this one's expensive to serve"}
