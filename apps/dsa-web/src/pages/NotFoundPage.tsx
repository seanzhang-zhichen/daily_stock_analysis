import type React from 'react';
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'motion/react';
import { Home } from 'lucide-react';

const NotFoundPage: React.FC = () => {
  const navigate = useNavigate();

  // Set page title
  useEffect(() => {
    document.title = '页面未找到 - DSA';
  }, []);

  return (
    <div className="relative flex min-h-[calc(100vh-5rem)] flex-col items-center justify-center overflow-hidden text-center px-4">
      {/* Subtle grid background */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            'linear-gradient(to right, hsl(var(--foreground)/0.04) 1px, transparent 1px), linear-gradient(to bottom, hsl(var(--foreground)/0.04) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
          WebkitMaskImage: 'radial-gradient(ellipse 70% 70% at 50% 50%, #000 40%, transparent 100%)',
          maskImage: 'radial-gradient(ellipse 70% 70% at 50% 50%, #000 40%, transparent 100%)',
        }}
      />

      {/* Ambient center glow */}
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[40vh] w-[40vw] -translate-x-1/2 -translate-y-1/2 rounded-full blur-[120px]"
        style={{ background: 'hsl(var(--primary) / 0.08)' }}
      />

      {/* 404 */}
      <motion.div
        initial={{ opacity: 0, y: -24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        className="relative mb-4"
      >
        <span
          className="text-[7rem] font-black leading-none tracking-tighter text-transparent bg-clip-text sm:text-[9rem]"
          style={{ backgroundImage: 'linear-gradient(135deg, hsl(var(--primary)) 0%, hsl(247 84% 66%) 100%)' }}
        >
          404
        </span>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.12, ease: 'easeOut' }}
        className="mb-8"
      >
        <h1 className="text-2xl font-bold text-foreground mb-2">页面未找到</h1>
        <p className="text-muted-text max-w-xs mx-auto">抱歉，您访问的页面不存在或已被移动</p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.22, ease: 'easeOut' }}
      >
        <button
          type="button"
          className="btn-primary inline-flex items-center gap-2"
          onClick={() => navigate('/')}
        >
          <Home className="h-4 w-4" />
          返回首页
        </button>
      </motion.div>
    </div>
  );
};

export default NotFoundPage;
