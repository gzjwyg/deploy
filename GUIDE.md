# The owner's guide to the server

How the Hong Kong server runs the Library, Nobody and the sign-in service, and the few things
only you can do. The design behind it is shared-account
[`docs/DEPLOYMENT.md`](https://github.com/gzjwyg/shared-account/blob/main/docs/DEPLOYMENT.md).

**In one paragraph.** A small program on the server, `lx-deploy`, looks at this repository every
two minutes. When an agent changes `release.json` (the list of versions to run), the server
downloads those versions, backs up what it is about to change, starts them, tests them, and puts
the previous versions back by itself if a test fails. It writes each result as a comment on this
repository's report issue. Agents can only choose versions; how the programs run (permissions,
folders, the web server, secrets) changes only when you run one command and type `yes`.

There are four parts. Part 1 can be done now and changes nothing. Parts 2 and 3 are once, about
an hour in all, when Claude says the release is ready. Part 4 is everyday use.

---

## Part 1. The read-only check (now, 5 minutes, changes nothing)

This tells Claude what is on the server today, especially what answers on ports 80 and 443
(the "Rat-webs 服务器已上线" page), before anything is installed.

1. On GitHub, open **gzjwyg/deploy → server → inspect.sh** and press the **Copy raw file**
   button (two overlapping squares, top right of the file).
2. Open the server's terminal (Tencent Cloud console → your Lighthouse instance → **Login**).
3. Type `cat > inspect.sh`, press Enter, paste (right-click → Paste), then press **Ctrl+D**.
4. Run:

   ```
   sudo bash inspect.sh > check.txt 2>&1; cat check.txt
   ```

5. Copy everything it printed and send it to Claude. It contains no passwords or keys. The last
   line says `Nothing was changed.`

---

## Part 2. Before installing (once, on GitHub and at your domain registrar)

Do these when Claude says `release.json` is ready for the first deployment.

### 2.1 Names (DNS)

At the company where you bought the domains, add **A records** pointing to the server's public
address (the one in the Lighthouse console):

| Name | For |
|---|---|
| `@` (that is, `lixingzhuyi.com` itself) and `www` | The homepage |
| `library` | The Library |
| `forum` | Nobody |
| `account` | Sign-in |
| `arena` | Leads to Nobody's Arena |
| `@` and `www` of `理性主义.com`, if you have that domain | Lead to the homepage |

`nobody.lixingzhuyi.com` is no longer used. Changes can take up to an hour to reach everyone.
The web server asks Let's Encrypt for the security certificates by itself once these exist.

### 2.2 The report issue

On GitHub, open **gzjwyg/deploy → Issues → New issue**, title it `Server reports`, and create
it. Note its number (probably 1). Every deployment result is posted there; you can subscribe to
it to get an email for each.

### 2.3 A token for downloading the programs

GitHub → your picture (top right) → **Settings** → **Developer settings** → **Personal access
tokens** → **Tokens (classic)** → **Generate new token (classic)**.

- Note: `lx server downloads images`
- Expiration: **No expiration** (or a year, and put a reminder in your calendar)
- Tick only **read:packages**
- **Generate token**, then copy it somewhere private for a few minutes. GitHub shows it once.

### 2.4 A token for the reports

Same page, **Fine-grained tokens** → **Generate new token**.

- Name: `lx server reports`; expiration as above
- Repository access: **Only select repositories** → **gzjwyg/deploy**
- Permissions → Repository permissions: **Commit statuses: Read and write**, **Issues: Read
  and write**. Nothing else.
- **Generate token** and copy it like the first.

### 2.5 Have these ready

- The QQ mailbox that sends sign-in codes and its **authorisation code** (授权码), as in
  shared-account `deploy/README.md` § 2. You can skip it during the install and add it later,
  but then nobody can sign in.
- A name and a password (12 to 72 characters) for the Library's administrator page.
- The email address where the server should send alerts (disk full, errors, old backups).

### 2.6 Snapshots

Tencent Cloud console → your Lighthouse instance → **Snapshots** → turn on **automatic
snapshots**. They are a copy of the whole disk, apart from the daily backups on it.

---

## Part 3. Installing (once, about 20 minutes)

Copy each grey block into the server's terminal and press Enter.

### 3.1 A read-only key for this repository

```
sudo install -d -m 700 /etc/lx-deploy
sudo ssh-keygen -q -t ed25519 -N '' -C lx-deploy-read-only -f /etc/lx-deploy/deploy-key
sudo cat /etc/lx-deploy/deploy-key.pub
```

Copy the line it prints (it starts with `ssh-ed25519`). On GitHub: **gzjwyg/deploy → Settings →
Deploy keys → Add deploy key**. Title `server (read-only)`, paste the line, leave **Allow write
access unticked**, **Add key**.

### 3.2 The server's copy of this repository

```
echo 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' | sudo tee /etc/lx-deploy/known_hosts
sudo install -d -m 700 /var/lib/lx-deploy
sudo git clone -c core.sshCommand='ssh -i /etc/lx-deploy/deploy-key -o IdentitiesOnly=yes -o UserKnownHostsFile=/etc/lx-deploy/known_hosts -o StrictHostKeyChecking=yes' git@github.com:gzjwyg/deploy.git /var/lib/lx-deploy/repo
```

The first line is GitHub's published identity (fingerprint
`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`), so the server can only talk to the real
GitHub. If the clone says "Permission denied", the deploy key of 3.1 is not added yet.

### 3.3 Install

```
sudo bash /var/lib/lx-deploy/repo/server/install.sh
```

It checks the server first and stops, changing nothing, if something is in the way (Part 5
says what to do). Then it asks, in this order:

1. `Go on?`: type `yes`.
2. The **read:packages** token (2.3): paste it and press Enter. Nothing appears while you
   paste; that is normal.
3. The **report** token (2.4), then the report issue's number (2.2).
4. The QQ address and its authorisation code; the Library's administrator name and password
   (twice); a DeepSeek and a Zhipu key for the Library's Ask feature (press Enter to leave Ask
   off); the alert address.
5. It then shows every server file it is about to install and asks `Apply these changes?`.
   Type `yes`.
6. The first deployment runs and prints its checks, about 20 lines ending in `pass` or `FAIL`.
   Send them to Claude either way.

The secrets are created on the server and kept only in `/etc/lx-deploy/env/` (readable by root
only). **One of them, `ACCOUNT_EMAIL_HASH_KEY`, must never change once people have accounts.**
Claude will ask you to keep a private copy of that folder off the server, for example on an
encrypted USB stick.

---

## Part 4. Everyday

**Nothing to do.** Deployments happen by themselves and are reported on the issue.

Commands you may be asked to run (each starts with `sudo`):

| Command | What it does |
|---|---|
| `sudo lx-deploy status` | What runs now, and whether server files are waiting for you |
| `sudo lx-deploy check` | The quick checks; `sudo lx-deploy check full` also stops and restarts things and restores each backup into a scratch folder (a few minutes) |
| `sudo lx-deploy rollback` | Go back to the previous versions |
| `sudo lx-deploy backup all` | Back up everything now (it also runs every day at 03:30 Hong Kong time) |
| `sudo lx-deploy apply-config` | Install changed server files: it shows each change and asks first |
| `sudo journalctl -u lx-deploy -n 100` | The updater's own messages |

**When Claude changes a server file** (the compose file, the web server's rules, a timer), the
report says "server file(s) wait for the owner". Run `sudo lx-deploy apply-config`, read what
it shows (or send it to Claude), and type `yes`. If the new web server rules do not validate,
it puts the old ones back by itself.

**Alerts** come by email from the sign-in mailbox: at most one per problem every six hours, in
plain words, with the first thing to try. Recoveries are quiet.

---

## Part 5. If something is in the way

### Something already answers on 80 or 443

The install stops if another program holds the web ports. On 2 Nov that was something showing
"Rat-webs 服务器已上线", probably a container from an earlier test. Send the install's message
(or the check of Part 1) to Claude. Usually the answer is to stop that container and keep it from
starting again, with two commands Claude will give you with its real name, then run the install
again. Nothing of it is deleted, so it can be started again if needed.

### Older manual installs

If an earlier guide (nginx and systemd) installed a service, it may hold port 8000, 8787 or
8790. The install stops and shows it. Claude will tell you how to stop the old service and move
its data into `/srv/lx/`, so nothing is lost.

### The article library

Its data (the owner's archive) is copied in separately with its own runbook (Article-database
`docs/operations.md`). Until then its daily backup says "not set up yet", and the alerts mention
that its backup is missing: that is expected.

---

## What can go wrong, and what cannot

- An agent can only choose which of our own published versions runs. It cannot change
  permissions, folders, the web server's rules or secrets, and it cannot log in to the server.
- A version that fails its checks is replaced by the previous one within minutes, and the report
  says so. If a version changed a database and the previous one cannot read it, the report shows
  the previous version failing too; then the backup taken just before the deployment (in
  `/srv/lx/backups/`) is restored, with Claude's help.
- The server holds three narrow credentials: the read-only key (reads this repository), the
  registry token (downloads our images) and the report token (posts on this repository). None can
  change any other repository. Your own SSH key is not involved.
