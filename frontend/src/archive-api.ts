import { Injectable, inject } from '@angular/core';
import { KEYCLOAK } from './identity';

@Injectable({ providedIn: 'root' })
export class ArchiveApi {
  private readonly keycloak = inject(KEYCLOAK);
  private readonly base = 'http://localhost:8000';

  async response(path: string, options: RequestInit = {}): Promise<Response> {
    await this.keycloak.updateToken(30);
    const headers = new Headers(options.headers);
    headers.set('Authorization', 'Bearer ' + this.keycloak.token);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(this.base + path, { ...options, headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail));
    }
    return response;
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    return (await this.response(path, options)).json() as Promise<T>;
  }

  async download(path: string, filename: string): Promise<void> {
    const response = await this.response(path);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
