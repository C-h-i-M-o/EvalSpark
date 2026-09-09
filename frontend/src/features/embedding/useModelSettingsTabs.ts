import { useLocation, useNavigate } from "react-router-dom";

export function useModelSettingsTabs() {
  const location = useLocation();
  const navigate = useNavigate();
  const activeKey = location.pathname === "/embedding-config" || new URLSearchParams(location.search).get("tab") === "embedding"
    ? "embedding" : "models";
  function changeTab(key: string): void {
    navigate(key === "embedding" ? "/models?tab=embedding" : "/models");
  }
  return { activeKey, changeTab };
}
