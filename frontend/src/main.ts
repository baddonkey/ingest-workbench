import { bootstrapApplication } from '@angular/platform-browser';
import Keycloak from 'keycloak-js';
import { AppComponent, KEYCLOAK } from './workbench';

const keycloak = new Keycloak({url:'http://localhost:8080',realm:'ingest-workbench',clientId:'archive-workbench'});
// Public SPA: Authorization Code + PKCE. Tokens remain in adapter memory.
keycloak.init({onLoad:'login-required',pkceMethod:'S256',checkLoginIframe:false})
  .then(() => bootstrapApplication(AppComponent,{providers:[{provide:KEYCLOAK,useValue:keycloak}]}))
  .catch(() => {document.body.textContent='Sign-in unavailable. Start Keycloak, then reload this page.';});
