import { Switch, Route, Router as WouterRouter, Redirect } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import CornersDashboard from "@/pages/CornersDashboard";
import CouponDashboard from "@/pages/CouponDashboard";
import BasketballDashboard from "@/pages/BasketballDashboard";
import MultiSportDashboard from "@/pages/MultiSportDashboard";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function Router() {
  return (
    <Switch>
      <Route path="/">{() => <Redirect to="/corners" />}</Route>
      <Route path="/corners" component={CornersDashboard} />
      <Route path="/coupons" component={CouponDashboard} />
      <Route path="/basketball" component={BasketballDashboard} />
      <Route path="/multi-sport" component={MultiSportDashboard} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
