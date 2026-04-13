import { Router, type IRouter } from "express";
import healthRouter from "./health";
import tipsRouter from "./tips";
import cornersRouter from "./corners";
import basketballRouter from "./basketball";
import ttRouter from "./tt";
import multiSportRouter from "./multi_sport";
import football25Router from "./football25";
import bttsRouter from "./btts";

const router: IRouter = Router();

router.use(healthRouter);
router.use(tipsRouter);
router.use(cornersRouter);
router.use(basketballRouter);
router.use(ttRouter);
router.use(multiSportRouter);
router.use(football25Router);
router.use(bttsRouter);

export default router;
