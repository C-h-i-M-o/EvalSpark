import { useRef, useState } from "react";
import { MenuFoldOutlined, MenuOutlined, PoweroffOutlined } from "@ant-design/icons";
import { Button, Drawer } from "antd";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import logoUrl from "../assets/logo.png";
import { useWorkspaceMotion } from "../animations/pageMotion";
import { BRAND_LOGO_ALT, BRAND_NAME } from "../config/brand";
import { useAuth } from "../features/auth/AuthContext";
import { getVisibleNavigationItems } from "../features/navigation/navigation";

export function AppLayout() {
  const auth = useAuth();
  const user = auth.user;
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  useWorkspaceMotion(mainRef, location.pathname);

  if (!user) {
    return null;
  }

  const renderNav = () => (
    <nav className="side-nav" aria-label="业务导航">
      {getVisibleNavigationItems(user).map((item) => (
        <NavLink key={item.path} to={item.path} end={item.path === "/"} onClick={() => setMobileNavOpen(false)}>
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
  const renderUserCard = () => (
    <div className="user-card">
      <span>{user.role === "admin" ? "管理员" : "普通用户"}</span>
      <strong>{user.username}</strong>
      <Button icon={<PoweroffOutlined />} type="primary" onClick={() => void auth.logout()}>
        退出登录
      </Button>
    </div>
  );

  return (
    <div className="workspace-layout" data-path={location.pathname}>
      <header className="mobile-topbar">
        <div className="mobile-brand">
          <img className="brand-logo" src={logoUrl} alt={BRAND_LOGO_ALT} />
          <strong>{BRAND_NAME}</strong>
        </div>
        <Button
          aria-label="打开导航"
          icon={mobileNavOpen ? <MenuFoldOutlined /> : <MenuOutlined />}
          onClick={() => setMobileNavOpen((open) => !open)}
        />
      </header>
      <aside className="sidebar">
        <div className="brand-block">
          <img className="brand-logo" src={logoUrl} alt={BRAND_LOGO_ALT} />
          <div>
            <p className="eyebrow">{BRAND_NAME}</p>
            <h1 className="layout-title">多模型评测</h1>
          </div>
        </div>

        {renderNav()}
        {renderUserCard()}
      </aside>

      <main ref={mainRef} className="workspace-main">
        <Outlet />
      </main>
      <Drawer
        className="mobile-nav-drawer"
        title={BRAND_NAME}
        placement="right"
        size="default"
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      >
        {renderNav()}
        {renderUserCard()}
      </Drawer>
    </div>
  );
}
