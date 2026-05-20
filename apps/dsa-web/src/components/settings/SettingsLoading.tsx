import type React from 'react';

export const SettingsLoading: React.FC = () => {
  return (
    <div className="space-y-4 animate-fade-in">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-[1.15rem] border border-border/60 bg-card/94 p-4 shadow-card">
          <div className="animate-pulse bg-foreground/10 h-3 w-32 rounded" />
          <div className="animate-pulse bg-foreground/5 mt-3 h-10 rounded-lg" />
        </div>
      ))}
    </div>
  );
};
