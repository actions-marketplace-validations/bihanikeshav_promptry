import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import Evals from "./pages/Evals";
import SuiteDetail from "./pages/SuiteDetail";
import RunDetail from "./pages/RunDetail";
import RunDiff from "./pages/RunDiff";
import Prompts from "./pages/Prompts";
import PromptDetail from "./pages/PromptDetail";
import Models from "./pages/Models";
import Cost from "./pages/Cost";
import Invocation from "./pages/Invocation";
import Feedback from "./pages/Feedback";
import Playground from "./pages/Playground";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Overview />} />
        <Route path="/evals" element={<Evals />} />
        <Route path="/suite/:name" element={<SuiteDetail />} />
        <Route path="/suite/:name/run/:runId" element={<RunDetail />} />
        <Route path="/suite/:name/diff" element={<RunDiff />} />
        <Route path="/prompts" element={<Prompts />} />
        <Route path="/prompts/:name" element={<PromptDetail />} />
        <Route path="/models" element={<Models />} />
        <Route path="/cost" element={<Cost />} />
        <Route path="/invocations/:id" element={<Invocation />} />
        <Route path="/feedback" element={<Feedback />} />
        <Route path="/playground" element={<Playground />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
