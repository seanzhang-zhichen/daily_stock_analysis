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
    <div className="relative min-h-screen bg-background text-foreground">
      {/* Ambient decorative background */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden" style={{ zIndex: 0 }}>
        <div className="absolute -right-[15%] -top-[15%] h-[60vh] w-[55vw] rounded-full blur-[160px]" style={{ background: 'hsl(var(--primary) / 0.05)' }} />
        <div className="absolute -bottom-[15%] -left-[15%] h-[50vh] w-[45vw] rounded-full blur-[140px]" style={{ background: 'hsl(247 84% 66% / 0.035)' }} />
        <div className="absolute top-[40%] left-[30%] h-[30vh] w-[30vw] rounded-full blur-[180px]" style={{ background: 'hsl(var(--primary) / 0.025)' }} />
      </div>
      <div className="pointer-events-none fixed inset-x-0 top-3 z-40 flex items-start justify-between px-3 lg:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="pointer-events-auto inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/60 bg-card/90 text-secondary-text shadow-soft-card backdrop-blur-md transition-all duration-200 hover:bg-hover hover:text-foreground hover:border-primary/20"
          aria-label="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="pointer-events-auto">
          <ThemeToggle />
        </div>
      </div>

      <div className="mx-auto flex min-h-screen w-full max-w-[1800px] px-3 py-3 sm:px-4 sm:py-4 lg:px-4">
        <aside
          className={cn(
            'sticky top-3 z-40 hidden shrink-0 overflow-visible rounded-2xl border border-[var(--shell-sidebar-border)] bg-card/85 p-2.5 shadow-soft-card-strong backdrop-blur-xl transition-[width] duration-300 lg:flex',
            'max-h-[calc(100vh-1.5rem)] self-start sm:top-4 sm:max-h-[calc(100vh-2rem)]',
            collapsed ? 'w-[68px]' : 'w-[220px]'
          )}
          aria-label="桌面侧边导航"
        >
          <SidebarNav collapsed={collapsed} onNavigate={() => setMobileOpen(false)} />
        </aside>

        <main className="relative min-h-0 min-w-0 flex-1 pt-14 lg:pl-3 lg:pt-0 touch-pan-y" style={{ zIndex: 1 }}>
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
