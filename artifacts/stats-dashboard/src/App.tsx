import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Dashboard from "@/pages/Dashboard";
import CornersDashboard from "@/pages/CornersDashboard";
import Football25Dashboard from "@/pages/Football25Dashboard";
import BasketballDashboard from "@/pages/BasketballDashboard";
import MultiSportDashboard from "@/pages/MultiSportDashboard";
import BTTSDashboard from "@/pages/BTTSDashboard";
import CricketDashboard from "@/pages/CricketDashboard";
import TTDashboard from "@/pages/TTDashboard";

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
      <Route path="/" component={Dashboard} />
      <Route path="/corners" component={CornersDashboard} />
      <Route path="/football25" component={Football25Dashboard} />
      <Route path="/basketball" component={BasketballDashboard} />
      <Route path="/multi-sport" component={MultiSportDashboard} />
      <Route path="/btts" component={BTTSDashboard} />
      <Route path="/cricket" component={CricketDashboard} />
      <Route path="/tt" component={TTDashboard} />
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
