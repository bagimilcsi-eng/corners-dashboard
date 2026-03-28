import { Router, type IRouter } from "express";

const router: IRouter = Router();

const SS_HEADERS = {
  "User-Agent": "SofaScore/242 CFNetwork/1568.100.1 Darwin/24.0.0",
  "Accept": "application/json",
  "Accept-Language": "hu-HU;q=1.0, en-US;q=0.9",
  "Connection": "keep-alive",
};

async function sofaFetch(path: string): Promise<any> {
  const url = `https://api.sofascore.app/api/v1${path}`;
  const res = await fetch(url, { headers: SS_HEADERS });
  if (!res.ok) throw new Error(`SofaScore ${res.status}: ${path}`);
  return res.json();
}

// Általános SofaScore proxy — minden bot ezt használja
router.use("/sofa", async (req: any, res) => {
  const path = req.path || "/";
  try {
    const data = await sofaFetch(path);
    res.json(data);
  } catch (e: any) {
    res.status(502).json({ error: e.message });
  }
});

export default router;
