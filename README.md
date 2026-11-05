<div align="center">

# Last Call

**E-mail your Jellyfin and Plex users what is leaving soon (via Maintainerr) and what is new, styled like a streaming service's newsletter.**

[![build](https://github.com/flowoy96/last-call/actions/workflows/docker.yml/badge.svg)](https://github.com/flowoy96/last-call/actions/workflows/docker.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-e11d48.svg)](LICENSE)
![Image: ghcr.io](https://img.shields.io/badge/image-ghcr.io%2Fflowoy96%2Flast--call-informational)

**🇩🇪 [Deutsch](#deutsch)** · **🇬🇧 [English](#english)**

<img src="docs/images/mail-desktop.png" alt="Example e-mail" width="420">

</div>

---

## Deutsch

### Was ist Last Call?

Last Call ist eine kleine, selbstgehostete Web-App. Sie schickt den Nutzerinnen und Nutzern deines **Jellyfin- und/oder Plex-Servers** regelmäßig eine E-Mail mit:

- **Bald nicht mehr verfügbar**: Titel aus deinen Maintainerr-Collections mit dem berechneten Löschdatum (Datum der Aufnahme in die Collection + „Take action after days“).
- **Neu hinzugefügt**: Filme, Serien und neue Folgen seit der letzten Mail.
- **Empfehlungen für dich** *(optional)*: noch nicht gesehene Filme und Serien, die zu den Genres passen, die die Person am meisten schaut.
- **Mal wieder schauen?** *(optional)*: eine Handvoll Filme, die die Person schon gesehen hat, aber schon länger nicht mehr (Standard: seit mindestens 180 Tagen).

Wer beide Server nutzt, bekommt wahlweise eine **kombinierte Mail** für Jellyfin und Plex oder nur die Mail für einen der beiden. Jede Person bekommt nur Titel aus Bibliotheken, auf die sie Zugriff hat. Leere Abschnitte fallen weg, und ganz leere Mails werden gar nicht erst verschickt.

### Funktionen

- Ein einziger Container (Python, Starlette, SQLite). Kein Datenbank-Container und kein JavaScript-Framework. Image für `amd64` und `arm64`.
- Einrichtung komplett im Web-UI, keine Secrets in Umgebungsvariablen nötig. API-Keys und Passwörter werden maskiert angezeigt.
- **Jellyfin und Plex**, getrennt einstellbar und einzeln abschaltbar, jeweils mit eigener (oder gemeinsamer) Maintainerr-Instanz.
- Empfänger per Klick aus Jellyfin und Plex importieren. Plex liefert die E-Mail-Adressen gleich mit. Gleichnamige Konten werden automatisch zu einer Person verknüpft. Pro Person wählst du: **beide Server in einer Mail**, nur Jellyfin oder nur Plex.
- Dunkles Mail-Design im Streaming-Look: Poster-Raster, Löschdatum als Badge, „Jetzt ansehen“-Deep-Links in Jellyfin. Tabellen-Layout mit Inline-CSS, responsiv, mit Outlook-Fallbacks.
- Poster werden verkleinert (ca. 5 KB pro Bild) und als CID-Anhang eingebettet. Jellyfin muss dafür nicht öffentlich erreichbar sein.
- **Keine Duplikate**: Derselbe Titel in mehreren Versionen (z. B. 1080p in „Filme“ und 4K in „Filme 4K“) erscheint nur einmal. Ein Film, dessen 1080p-Kopie gelöscht wird, während die 4K-Kopie bleibt, steht nicht unter „Bald weg“. Eine neue 4K-Version eines alten Films zählt nicht als „neu“. Kein Titel erscheint in zwei Abschnitten, und Vorschläge wiederholen sich für eine einstellbare Zeit nicht (Standard: 90 Tage).
- Zeitplan: täglich, wöchentlich, alle 2 Wochen oder monatlich, Wochentag und Uhrzeit frei wählbar. Mails auf Deutsch oder Englisch.
- Vorschau pro Person im Browser, „Jetzt senden“, Testmail, Verbindungstests und ein Versandprotokoll.
- Sicherheit: Admin-Login mit scrypt-Hash, CSRF-Schutz, Login-Rate-Limit, Security-Header, Container läuft als Non-Root.

### Screenshots

| Dashboard | Einstellungen |
|---|---|
| <img src="docs/images/dashboard.png" alt="Dashboard" width="420"> | <img src="docs/images/settings.png" alt="Einstellungen" width="420"> |
| **Empfänger** | **Mail-Vorschau** |
| <img src="docs/images/recipients.png" alt="Empfänger" width="420"> | <img src="docs/images/preview.png" alt="Vorschau" width="420"> |

<details><summary>Mail auf dem Smartphone</summary>
<img src="docs/images/mail-mobile.png" alt="Mail mobil" width="320">
</details>

> Alle Titel und Poster in den Screenshots sind frei erfunden und automatisch generiert.

### Quickstart mit Portainer

1. In Portainer **Stacks → Add stack → Web editor** öffnen.
2. Diesen Stack einfügen und **Deploy the stack** klicken:

```yaml
services:
  last-call:
    image: ghcr.io/flowoy96/last-call:latest
    container_name: last-call
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - TZ=Europe/Berlin
      - LASTCALL_SECURE_COOKIES=0
    volumes:
      - last-call-data:/data
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  last-call-data:
```

3. `http://<host>:8080` öffnen und den Admin-Account anlegen.
4. Unter **Settings** Jellyfin und/oder Plex, Maintainerr und SMTP eintragen, speichern und mit den Test-Buttons prüfen.
5. Unter **Recipients** die User aus Jellyfin und/oder Plex importieren, E-Mail-Adressen prüfen, pro Person den Server bzw. „beide“ wählen und aktivieren.
6. Unter **Preview** die Mail ansehen und bei Bedarf sofort senden.

Ohne Docker Compose geht es auch so:

```bash
docker run -d --name last-call -p 8080:8080 -e TZ=Europe/Berlin -v last-call-data:/data --restart unless-stopped ghcr.io/flowoy96/last-call:latest
```

### Konfiguration

Alles Wesentliche wird im Web-UI eingestellt und in `/data/lastcall.db` (SQLite) gespeichert.

| Einstellung | Hinweis |
|---|---|
| Jellyfin-URL (intern) | Adresse, unter der Last Call Jellyfin erreicht, z. B. `http://jellyfin:8096` |
| Jellyfin-API-Key | Jellyfin → Dashboard → API-Schlüssel → `+` |
| Öffentliche Jellyfin-URL | Wird für die Links in der Mail verwendet, z. B. `https://jellyfin.example.com` |
| Maintainerr-URL (je Server) | z. B. `http://maintainerr:6246`. Für Jellyfin und Plex getrennt einstellbar. Eine Instanz, die beide bedient, trägst du einfach zweimal ein: Last Call trennt die Collections nach Server-Typ. Maintainerr hat selbst keine API-Authentifizierung. Das optionale API-Key-Feld wird als `X-Api-Key`-Header mitgeschickt, etwa für einen Reverse Proxy. |
| Plex-Server-URL | Adresse, unter der Last Call Plex erreicht, z. B. `http://plex:32400` |
| Plex-Token | Token des Server-Besitzers ([so findest du ihn](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)). Damit holt Last Call bei plex.tv die Liste der freigegebenen User samt E-Mail-Adresse und deren Zugriffs-Token für deinen Server. |
| Öffentliche Plex-URL | Optional. Leer: Links öffnen `app.plex.tv`. Gesetzt (z. B. `https://plex.example.com`): Links öffnen deinen Server direkt. |
| SMTP | Host, Port, STARTTLS/SSL/keine, Benutzer, Passwort, Absendername und -adresse |
| Zeitplan | Täglich, wöchentlich, alle 2 Wochen (gerade Kalenderwochen) oder monatlich (erster gewählter Wochentag im Monat), feste Uhrzeit (Zeitzone aus `TZ`) |
| Empfehlungen | An/aus und Anzahl (Standard: 6, halb Filme, halb Serien) |
| Mal wieder schauen | An/aus, Anzahl (Standard: 3) und Mindestabstand seit dem letzten Ansehen (Standard: 180 Tage) |
| Wiederholungssperre | So viele Tage wird ein bereits vorgeschlagener oder als neu gemeldeter Titel nicht erneut vorgeschlagen (Standard: 90) |
| Nur Vorschläge senden | Aus (Standard): Eine Mail geht nur raus, wenn etwas bald gelöscht wird oder neu ist. An: Auch reine Vorschlags-Mails werden verschickt. |

Umgebungsvariablen (alle optional):

| Variable | Standard | Bedeutung |
|---|---|---|
| `TZ` | `Europe/Berlin` | Zeitzone für Zeitplan und Löschdaten |
| `LASTCALL_SECURE_COOKIES` | `0` | Auf `1` setzen, wenn Last Call nur per HTTPS erreichbar ist |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP deines Reverse Proxys, damit Last Call die echte Client-IP (für das Login-Rate-Limit) sieht |
| `LOG_LEVEL` | `INFO` | Log-Level |

**So wird das Löschdatum berechnet:** Für jede aktive Maintainerr-Collection mit einer Aktion, die Dateien entfernt (*Delete*, *Unmonitor and delete*, *Delete show if empty*), und mit gesetztem *Take action after days* gilt: Datum der Aufnahme in die Collection + Tage. Steht ein Titel in mehreren Collections, gilt das früheste Datum. Collections mit *Do nothing*, *Unmonitor* oder *Change quality profile* werden ignoriert.

**Plex und Jellyfin in einer Mail:** Bei „beide Server“ werden die Bibliotheken beider Server zusammengeführt. Ein Titel, der auf beiden liegt, erscheint nur einmal. Ein Titel, der auf einem Server gelöscht wird, aber auf dem anderen bleibt, steht nicht unter „Bald weg“. Was jemand auf einem Server gesehen hat, wird auf dem anderen nicht empfohlen. Jede Karte zeigt, auf welchem Server der Titel liegt, und „Jetzt ansehen“ öffnet den passenden Server.

**„Neu hinzugefügt“** umfasst alles seit der letzten erfolgreich versendeten Mail an diese Person. Bei der ersten Mail wird so viele Tage zurückgeschaut, wie in den Einstellungen festgelegt ist (Standard: 7).

**So funktionieren die Empfehlungen:** Last Call bildet aus den gesehenen und favorisierten Titeln der Person ein Genre-Profil. Favoriten zählen stärker, kürzlich Gesehenes ebenfalls. Damit bewertet es alle noch nicht angefangenen Filme und Serien, rechnet die Community-Bewertung leicht dazu und wählt dann gewichtet zufällig aus. So kommen nicht jedes Mal dieselben Titel. Angefangene Serien und Filme, die in einer anderen Version schon gesehen wurden, werden nicht empfohlen. Die Auswahl ist pro Person und Tag fest, die Vorschau zeigt also genau das, was am selben Tag verschickt würde.

**So funktioniert „Mal wieder schauen?“:** Kandidaten sind Filme mit Status „gesehen“, deren letzte Wiedergabe länger als der eingestellte Mindestabstand zurückliegt. Alle Kopien eines Films zählen dabei gemeinsam. Favoriten, gut bewertete und länger nicht gesehene Filme werden bevorzugt.

### Update

In Portainer den Stack öffnen und **Pull and redeploy** wählen, bzw. per CLI:

```bash
docker compose pull && docker compose up -d
```

Die Daten im Volume `/data` bleiben dabei erhalten.

### FAQ

**Muss Jellyfin/Plex öffentlich erreichbar sein?**
Nein. Poster werden eingebettet. Nur die „Jetzt ansehen“-Links zeigen auf die öffentliche URL. Ohne öffentliche URL funktionieren sie nur im Heimnetz.

**Löscht Last Call etwas?**
Nein. Last Call liest nur. Gelöscht wird ausschließlich von Maintainerr.

**Gibt es OIDC/SSO (z. B. Authentik)?**
Nicht eingebaut, um die App schlank zu halten. Stell Last Call hinter deinen Reverse Proxy mit Forward Auth (Authentik-Proxy-Provider, Authelia …). Der eingebaute Admin-Login bleibt zusätzlich aktiv.

**Welche Plex-User werden importiert?**
Der Besitzer des Servers und alle User, mit denen der Server geteilt ist (laut plex.tv). Plex-Home-User ohne eigenes plex.tv-Konto werden nicht importiert.

**Warum ist die Weboberfläche englisch?**
Damit der Code klein bleibt. Die E-Mails gibt es auf Deutsch und Englisch.

**Ich nutze einen Bind-Mount statt eines Volumes und bekomme „permission denied“.**
Der Container läuft als UID/GID `1000`. Setze die Rechte entsprechend: `chown -R 1000:1000 /pfad/zu/data`.

**Warum taucht ein Film mit mehreren Qualitäten nur einmal auf?**
Versionen im selben Ordner fasst Jellyfin selbst zu einem Eintrag zusammen. Liegen sie in verschiedenen Bibliotheken, erkennt Last Call sie über TMDb-, IMDb- und TVDB-IDs bzw. Titel und Jahr. Damit das zuverlässig klappt, sollten die Metadaten-Provider in Jellyfin aktiv sein.

**Wie groß werden die Mails?**
Ein Poster ist etwa 4–6 KB groß. Eine Mail mit 30 Titeln hat also rund 150–200 KB.

**Wird die Mail in Gmail, Outlook und Apple Mail richtig dargestellt?**
Das Template verwendet die üblichen E-Mail-Techniken: Tabellen, Inline-Styles, Fluid-Hybrid-Raster, MSO-Conditional-Comments und einen VML-Button. Falls in deinem Client etwas komisch aussieht, eröffne bitte ein Issue mit Screenshot.

### Lizenz und Haftungsausschluss

[MIT](LICENSE). Die Software wird „wie besehen“ ohne Gewährleistung bereitgestellt, die Nutzung erfolgt auf eigenes Risiko. Es gibt keine Haftung für Schäden, Datenverlust oder versehentliche Löschungen. Dieses Projekt ist nicht mit Jellyfin, Plex, Maintainerr oder anderen genannten Projekten verbunden und wird von ihnen nicht unterstützt. Marken gehören ihren Inhabern. Last Call stellt keine Medieninhalte bereit und ist nicht für die Verbreitung urheberrechtlich geschützter Inhalte gedacht. Du bist selbst für die Rechtmäßigkeit deiner Mediathek verantwortlich. Als Betreiberin oder Betreiber bist du außerdem selbst für den Datenschutz (DSGVO) beim Speichern von E-Mail-Adressen und beim Versand verantwortlich, einschließlich der Einwilligung der Empfängerinnen und Empfänger. Details stehen in [DISCLAIMER.md](DISCLAIMER.md).

---

## English

### What is Last Call?

Last Call is a small self-hosted web app. It periodically e-mails the users of your **Jellyfin and/or Plex server**:

- **Leaving soon**: titles from your Maintainerr collections with their calculated removal date (date added to the collection + "take action after days").
- **Recently added**: movies, shows and new episodes since their last e-mail.
- **Recommended for you** *(optional)*: unwatched movies and shows that match the genres the user watches most.
- **Watch it again?** *(optional)*: a handful of movies the user has seen, but not for a long time (default: at least 180 days ago).

People who use both servers get either **one combined mail** for Jellyfin and Plex or a mail for just one of them, as you choose. Each user only sees titles from libraries they can access. Empty sections are left out, and completely empty e-mails are never sent.

### Features

- One container (Python, Starlette, SQLite). No database container and no JavaScript framework. `amd64` and `arm64` images.
- Configured entirely in the web UI, so no secrets are needed in environment variables. API keys and passwords are masked.
- **Jellyfin and Plex**, configured separately and individually switchable, each with its own (or a shared) Maintainerr instance.
- Import recipients from Jellyfin and Plex with one click. Plex provides e-mail addresses. Accounts with the same name are linked to one person automatically. Per person you choose: **both servers in one mail**, Jellyfin only or Plex only.
- Dark, streaming-style e-mail: poster grid, removal date badge, "Watch now" deep links into Jellyfin. Table layout with inline CSS, responsive, with Outlook fallbacks.
- Posters are resized (around 5 KB each) and embedded as CID attachments, so Jellyfin does not need to be publicly reachable.
- **No duplicates**: the same title in several versions (e.g. 1080p in "Movies" and 4K in "Movies 4K") is listed once. A movie whose 1080p copy is deleted while the 4K copy stays is not shown as leaving. A new 4K version of an old movie does not count as "new". No title appears in two sections, and suggestions are not repeated for a configurable time (default: 90 days).
- Schedule: daily, weekly, every 2 weeks or monthly, on the weekday and at the time you choose. E-mails in German or English.
- Per-user preview in the browser, "send now", test e-mail, connection tests and a send log.
- Security: admin login with scrypt hash, CSRF protection, login rate limit, security headers, non-root container.

### Screenshots

See the [screenshots above](#screenshots). All titles and posters shown are made up and generated automatically.

### Quickstart with Portainer

1. In Portainer go to **Stacks → Add stack → Web editor**.
2. Paste [`docker-compose.yml`](docker-compose.yml) (same content as in the German section) and click **Deploy the stack**.
3. Open `http://<host>:8080` and create the admin account.
4. In **Settings**, enter Jellyfin and/or Plex, Maintainerr and SMTP, save, and use the test buttons.
5. In **Recipients**, import users from Jellyfin and/or Plex, check the e-mail addresses, choose a server (or both) per person and enable them.
6. In **Preview**, check the e-mail and send it right away if you like.

### Configuration

Everything is configured in the web UI and stored in `/data/lastcall.db` (SQLite).

| Setting | Notes |
|---|---|
| Jellyfin URL (internal) | How Last Call reaches Jellyfin, e.g. `http://jellyfin:8096` |
| Jellyfin API key | Jellyfin → Dashboard → API Keys → `+` |
| Public Jellyfin URL | Used for links in the e-mail, e.g. `https://jellyfin.example.com` |
| Maintainerr URL (per server) | e.g. `http://maintainerr:6246`, set separately for Jellyfin and Plex. If one instance serves both, enter it twice: Last Call splits the collections by server type. Maintainerr has no API authentication of its own. The optional API key is sent as an `X-Api-Key` header, e.g. for a reverse proxy. |
| Plex server URL | How Last Call reaches Plex, e.g. `http://plex:32400` |
| Plex token | The server owner's token ([how to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)). Last Call uses it to get the list of shared users, with their e-mail addresses and access tokens for your server, from plex.tv. |
| Public Plex URL | Optional. Empty: links open `app.plex.tv`. Set (e.g. `https://plex.example.com`): links open your server directly. |
| SMTP | Host, port, STARTTLS/SSL/none, user, password, sender name and address |
| Schedule | Daily, weekly, every 2 weeks (even calendar weeks) or monthly (first chosen weekday of the month), at a fixed time (time zone from `TZ`) |
| Recommendations | On/off and number (default: 6, half movies, half shows) |
| Watch it again | On/off, number (default: 3) and minimum time since last watched (default: 180 days) |
| Repeat cooldown | Days before a title that was already suggested or announced as new is suggested again (default: 90) |
| Send suggestions only | Off (default): a mail is only sent when something is leaving or new. On: mails with only suggestions are sent too. |

Environment variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `Europe/Berlin` | Time zone for the schedule and removal dates |
| `LASTCALL_SECURE_COOKIES` | `0` | Set to `1` if Last Call is only served over HTTPS |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Your reverse proxy's IP, so the real client IP is used for the login rate limit |
| `LOG_LEVEL` | `INFO` | Log level |

**How the removal date is calculated:** for every active Maintainerr collection whose action removes files (*Delete*, *Unmonitor and delete*, *Delete show if empty*) and that has *take action after days* set, the date is: date added to the collection + days. If a title is in several collections, the earliest date wins. Collections with *Do nothing*, *Unmonitor* or *Change quality profile* are ignored.

**Plex and Jellyfin in one mail:** with "both servers", the libraries of both servers are merged. A title on both servers is listed once. A title that is deleted on one server but stays on the other is not shown as leaving. Anything a person watched on one server is not recommended on the other. Each card shows which server the title is on, and "Watch now" opens the right server.

**"Recently added"** covers everything since the last e-mail successfully sent to that user. For their first e-mail, Last Call looks back the number of days configured in the settings (default: 7).

**How recommendations work:** Last Call builds a genre profile from the titles the user has watched or marked as favourite. Favourites count more, and so does anything watched recently. It scores every movie and show the user hasn't started, adds a little weight for the community rating, and picks titles with weighted randomness, so the same ones don't come up every time. Shows the user has started, and movies already watched in another version, are never recommended. The picks are fixed per user and day, so the preview shows exactly what would be sent that day.

**How "Watch it again?" works:** candidates are movies marked as played whose last playback is older than the configured minimum. All copies of a movie count together. Favourites, well-rated movies and movies not seen for a long time are preferred.

### Updating

In Portainer open the stack and choose **Pull and redeploy**, or run:

```bash
docker compose pull && docker compose up -d
```

Your data in the `/data` volume is kept.

### FAQ

**Does Jellyfin/Plex need to be publicly reachable?**
No. Posters are embedded. Only the "Watch now" links point to the public URL. Without one, they only work inside your home network.

**Does Last Call delete anything?**
No. It only reads data. Deleting is done exclusively by Maintainerr.

**OIDC/SSO (e.g. Authentik)?**
Not built in, to keep the app small. Put Last Call behind your reverse proxy with forward auth (Authentik proxy provider, Authelia, …). The built-in admin login stays active as well.

**Which Plex users are imported?**
The server owner and every user the server is shared with (according to plex.tv). Plex Home users without their own plex.tv account are not imported.

**Why is the web UI in English?**
To keep the code small. The e-mails are available in German and English.

**I use a bind mount and get "permission denied".**
The container runs as UID/GID `1000`: `chown -R 1000:1000 /path/to/data`.

**Why does a movie with several qualities only show up once?**
Jellyfin already merges versions in the same folder. For copies in different libraries, Last Call matches them by TMDb, IMDb and TVDB IDs, or by title and year. Keep the metadata providers enabled in Jellyfin so this works reliably.

**How big are the e-mails?**
About 4–6 KB per poster, so an e-mail with 30 titles is roughly 150–200 KB.

**Does the e-mail render correctly in Gmail, Outlook and Apple Mail?**
The template uses the usual e-mail techniques: tables, inline styles, a fluid-hybrid grid, MSO conditional comments and a VML button. If something looks off in your client, please open an issue with a screenshot.

### License and disclaimer

[MIT](LICENSE). The software is provided "as is", without warranty of any kind, and you use it at your own risk. There is no liability for damages, data loss or accidental deletions. This project is not affiliated with or endorsed by Jellyfin, Plex, Maintainerr or any other project mentioned. Trademarks belong to their owners. Last Call provides no media content and is not intended for distributing copyrighted material. You are responsible for the legality of your media library. As an operator, you are also responsible for data protection (GDPR) when storing e-mail addresses and sending e-mails, including obtaining your recipients' consent. See [DISCLAIMER.md](DISCLAIMER.md).

Third-party: [Pico CSS](https://picocss.com) (MIT) is bundled in `app/static/`.
