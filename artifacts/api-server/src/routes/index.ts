import { Router, type IRouter } from "express";
import healthRouter from "./health";
import tipsRouter from "./tips";

const router: IRouter = Router();

router.use(healthRouter);
router.use(tipsRouter);

export default router;
