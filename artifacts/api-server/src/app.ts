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
