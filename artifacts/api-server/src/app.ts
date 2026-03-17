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

const botFiles: Record<string, string> = {
  "main.py": path.resolve(process.cwd(), "../../main.py"),
  "corners_bot.py": path.resolve(process.cwd(), "../../corners_bot.py"),
  "requirements.txt": path.resolve(process.cwd(), "../../requirements.txt"),
};

for (const prefix of ["/api/download", "/api-server/api/download"]) {
  app.get(`${prefix}/:file`, (req, res) => {
    const filename = req.params.file;
    const filepath = botFiles[filename];
    if (!filepath || !fs.existsSync(filepath)) {
      return res.status(404).send("Not found");
    }
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.sendFile(filepath);
  });

  app.get(prefix, (_req, res) => {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.send(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Bot fájlok letöltése</title>
<style>body{font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px}
h2{margin-bottom:24px}a{display:block;padding:14px 20px;margin:10px 0;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;font-size:16px;text-align:center}
a:hover{background:#1d4ed8}p{color:#555;margin-bottom:24px}</style></head>
<body><h2>&#11015;&#65039; Bot fájlok letöltése</h2>
<p>Kattintson a fájlra a letöltéshez:</p>
<a href="/api/download/main.py">&#128196; main.py (Asztalitenisz bot)</a>
<a href="/api/download/corners_bot.py">&#128196; corners_bot.py (Szöglet bot)</a>
<a href="/api/download/requirements.txt">&#128196; requirements.txt (Csomagok)</a>
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
