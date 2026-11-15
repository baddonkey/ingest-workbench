"""Bounded HTTP transport for the external KOST-Val service."""
import uuid
import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ReportError(BaseModel):
    model_config = ConfigDict(extra='ignore')
    module: str = Field(max_length=1000)
    message: str = Field(max_length=16000)


class Job(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: uuid.UUID
    status: Literal['queued', 'running', 'valid', 'invalid', 'error']
    message: str | None = Field(default=None, max_length=16000)
    errors: list[ReportError] = Field(default_factory=list, max_length=2000)
    exit_code: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    report_url: str | None = None


class KostClient:
    def __init__(self, url, api_key='', *, transport=None):
        self.client = httpx.Client(base_url=url.rstrip('/')+'/',
            headers={'X-API-Key': api_key} if api_key else {},
            timeout=httpx.Timeout(connect=10, read=60, write=120, pool=10),
            follow_redirects=False, transport=transport)

    def close(self):
        self.client.close()

    def bounded(self, method, path, limit, **kwargs):
        with self.client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError('KOST service redirects are not supported')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError('KOST response exceeds local size limit')
            return bytes(data)

    def job(self, method, path, **kwargs):
        raw = self.bounded(method, path, 1024*1024, **kwargs)
        return Job.model_validate_json(raw).model_dump(mode='json'), raw

    def submit(self, data, filename, language='de'):
        return self.job('POST', 'validations',
            files={'sip': (filename, data, 'application/zip')},
            data={'language': language, 'log_type': 'xml'})

    def status(self, job_id):
        return self.job('GET', 'validations/'+str(uuid.UUID(job_id)))

    def report(self, job_id):
        return self.bounded('GET', 'validations/'+str(uuid.UUID(job_id))+'/report', 16*1024*1024)