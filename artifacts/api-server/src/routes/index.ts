import { Router, type IRouter } from "express";
import healthRouter from "./health";
import tipsRouter from "./tips";
import cornersRouter from "./corners";
import couponsRouter from "./coupons";
import basketballRouter from "./basketball";
import ttRouter from "./tt";
import multiSportRouter from "./multi_sport";

const router: IRouter = Router();

router.use(healthRouter);
router.use(tipsRouter);
router.use(cornersRouter);
router.use(couponsRouter);
router.use(basketballRouter);
router.use(ttRouter);
router.use(multiSportRouter);

export default router;
