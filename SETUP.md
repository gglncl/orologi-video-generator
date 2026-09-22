# Orologi Video Generator — setup webapp

## 1. Carica nel repository
Carica/sostituisci nella root:
- `index.html`
- `app.js`
- `styles.css`
- `manifest.json`
- `icon.svg`
- `generate_video.py`

Sostituisci anche:
- `.github/workflows/generate-watch-video.yml`

`worker.js` NON contiene segreti: serve solo per creare il Worker Cloudflare.

## 2. GitHub Pages
Repository → Settings → Pages → Source: **GitHub Actions**.

Poi Actions → Generate Watch Video → Run workflow:
- request_id: `firstdeploy`
- mode: `deploy_only`
- lascia il resto com'è

Al termine la webapp sarà su:
`https://gglncl.github.io/orologi-video-generator/`

## 3. GitHub token per il Worker
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.

- Repository access: **Only select repositories** → `orologi-video-generator`
- Repository permissions → **Actions: Read and write**
- Metadata: Read (automatico)

Copia il token appena creato. Non salvarlo nel repository.

## 4. Cloudflare Worker
Cloudflare → Workers & Pages → Create → Worker.

Incolla tutto `worker.js`, salva e fai Deploy.

Worker → Settings → Variables and Secrets → aggiungi due **Secrets**:
- `GITHUB_TOKEN` = token GitHub creato sopra
- `APP_PIN` = un PIN privato scelto da te, almeno 4 caratteri

Fai Deploy di nuovo se richiesto.

## 5. Collega la webapp
Apri:
`https://gglncl.github.io/orologi-video-generator/`

Nelle impostazioni inserisci:
- Worker URL, ad esempio `https://orologi-video-generator.<tuo-subdomain>.workers.dev`
- lo stesso `APP_PIN`

Da quel momento l'uso quotidiano è:
1. incolla script;
2. hook;
3. opzionale Prova voce;
4. GENERA VIDEO;
5. scarica MP4.

La chiave Pexels resta nel secret GitHub `PEXELS_API_KEY` già configurato.
