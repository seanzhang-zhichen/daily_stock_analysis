import type React from 'react';
import { useEffect, useState } from 'react';
import { Menu } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { cn } from '../../utils/cn';
import { ThemeToggle } from '../theme/ThemeToggle';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const collapsed = false;

  useEffect(() => {
    if (!mobileOpen) {
      return undefined;
    }

    const handleResize = () => {
      if (window.innerWidth >= 1024) {
        setMobileOpen(false);
      }
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [mobileOpen]);

  return (
    <div className="app-shell relative">
      <div className="app-mobile-toolbar lg:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="ui-button ui-button-size-md ui-button-secondary w-10 px-0"
          aria-label="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="pointer-events-auto">
          <ThemeToggle />
        </div>
      </div>

      <div className="app-shell-frame">
        <aside
          className={cn(
            'app-shell-sidebar transition-[width] duration-300',
            collapsed ? 'w-[68px]' : 'w-[220px]'
          )}
          aria-label="桌面侧边导航"
        >
          <SidebarNav collapsed={collapsed} onNavigate={() => setMobileOpen(false)} />
        </aside>

        <main className="app-shell-main">
          {children ?? <Outlet />}
        </main>
      </div>

      <Drawer
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        title="导航菜单"
        width="max-w-[280px]"
        zIndex={90}
        side="left"
      >
        <SidebarNav onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </div>
  );
};
