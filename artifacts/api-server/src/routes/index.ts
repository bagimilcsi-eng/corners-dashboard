import { Router, type IRouter } from "express";
import healthRouter from "./health";
import tipsRouter from "./tips";
import cornersRouter from "./corners";

const router: IRouter = Router();

router.use(healthRouter);
router.use(tipsRouter);
router.use(cornersRouter);

export default router;
