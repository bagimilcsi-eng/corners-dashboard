import express, { type Express } from "express";
import cors from "cors";
import path from "path";
import fs from "fs";
import router from "./routes";

const app: Express = express();

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);
app.use("/api-server/api", router);

app.get("/ping", (_req, res) => {
  res.status(200).send("pong");
});
app.get("/api-server/ping", (_req, res) => {
  res.status(200).send("pong");
});

const botFiles: Record<string, string> = {
  "main.py": path.resolve(process.cwd(), "../../main.py"),
  "corners_bot.py": path.resolve(process.cwd(), "../../corners_bot.py"),
  "requirements.txt": path.resolve(process.cwd(), "../../requirements.txt"),
  "bots.zip": path.resolve(process.cwd(), "bots.zip"),
};

for (const prefix of ["/api/download", "/api-server/api/download"]) {
  app.get(`${prefix}/:file`, (req, res) => {
    const filename = req.params.file;
    const filepath = botFiles[filename];
    if (!filepath || !fs.existsSync(filepath)) {
      return res.status(404).send("Not found");
    }
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    if (filename.endsWith(".zip")) {
      res.setHeader("Content-Type", "application/zip");
    } else {
      res.setHeader("Content-Type", "text/plain; charset=utf-8");
    }
    res.sendFile(filepath);
  });

  app.get(prefix, (_req, res) => {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.send(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bot fájlok letöltése</title>
<style>
body{font-family:sans-serif;max-width:520px;margin:60px auto;padding:20px}
h2{margin-bottom:8px}
.sub{color:#666;margin-bottom:28px;font-size:15px}
.zip-btn{display:block;padding:16px 20px;margin:0 0 24px;background:#16a34a;color:#fff;border-radius:10px;text-decoration:none;font-size:17px;font-weight:bold;text-align:center}
.zip-btn:hover{background:#15803d}
hr{border:none;border-top:1px solid #ddd;margin:0 0 18px}
a{display:block;padding:12px 18px;margin:8px 0;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-size:15px;text-align:center}
a:hover{background:#1d4ed8}
</style></head>
<body>
<h2>&#11015;&#65039; Bot fájlok letöltése</h2>
<p class="sub">Töltse le az összes fájlt egyszerre (ZIP) vagy egyenként:</p>
<a class="zip-btn" href="/api/download/bots.zip">&#128230; Letöltés ZIP-ben (mind a 3 fájl)</a>
<hr>
<a href="/api/download/main.py">&#128196; main.py &mdash; Asztalitenisz bot</a>
<a href="/api/download/corners_bot.py">&#128196; corners_bot.py &mdash; Szöglet bot</a>
<a href="/api/download/requirements.txt">&#128196; requirements.txt &mdash; Csomagok listája</a>
</body></html>`);
  });
}

const staticDirCandidates = [
  path.resolve(process.cwd(), "artifacts/stats-dashboard/dist/public"),
  path.resolve(process.cwd(), "../../artifacts/stats-dashboard/dist/public"),
];

const staticDir = staticDirCandidates.find((p) => fs.existsSync(p));

if (staticDir) {
  app.use(express.static(staticDir));
  app.use((_req, res) => {
    res.sendFile(path.join(staticDir, "index.html"));
  });
}

export default app;
