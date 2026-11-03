# deploy

How the owner's Hong Kong server runs the ecosystem: Rat-webs (the Library), Nobody (the forum),
the shared account service, and the Concept Map's and Article-database's jobs. Maintained by Claude.

| Path | What | State |
|---|---|---|
| `home/` | The homepage at `lixingzhuyi.com`: what the three parts are and the way into each (`docs/IDENTITY-V2.md` § 1 in shared-account) | Ready |
| `lx-deploy`, compose, `Caddyfile`, `watch.json`, timers, the owner's guide | Deployment | Waits on the owner's read-only server check |

Nothing here changes the server by itself. The owner decides when anything is installed.

## The homepage

Static files only: no scripts, no outside fonts or files (Google Fonts and most foreign CDNs are
blocked in mainland China), light and dark colour schemes, Chinese at `/` and English at `/en/`.
To change the wording, edit `home/index.html` and `home/en/index.html` together; the styles are
in `home/home.css`.

Caddy will serve it like this (the full `Caddyfile` comes with the deployment):

```
lixingzhuyi.com {
	root * /srv/lx/home
	file_server
	handle_errors {
		rewrite * /404.html
		file_server
	}
}
www.lixingzhuyi.com, 理性主义.com, www.理性主义.com {
	redir https://lixingzhuyi.com{uri} permanent
}
arena.lixingzhuyi.com {
	redir https://forum.lixingzhuyi.com/arena{uri} temporary
}
```

The cards link to `library.`, `forum.` and `arena.lixingzhuyi.com`, so they only work once those
DNS records exist (`IDENTITY-V2.md` § 2).
