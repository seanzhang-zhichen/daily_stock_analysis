import type React from 'react';
import { useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { PenLine } from 'lucide-react';
import { StandardPageLayout } from '../components/common/PageLayouts';
import { SettingsAlert } from '../components/settings';
import { ResearchReportsStudio } from '../components/research/ResearchReportsStudio';
import { useAuth } from '../hooks';

const ResearchReportsStudioPage: React.FC = () => {
  const { userMode } = useAuth();
  const userModeEnabled = Boolean(userMode?.userModeEnabled);
  const loggedIn = Boolean(userMode?.loggedIn);
  const isResearchOperator = Boolean(userMode?.user?.isResearchOperator);

  useEffect(() => {
    document.title = '研报工作台 - DSA';
  }, []);

  if (!userModeEnabled) {
    return <Navigate to="/settings" replace />;
  }
  if (!loggedIn) {
    return <Navigate to="/login?redirect=%2Fresearch-reports%2Fstudio" replace />;
  }
  if (!isResearchOperator) {
    return (
      <StandardPageLayout>
        <SettingsAlert
          title="无权访问"
          message="当前账号没有研报运营身份。请先由服务器侧授予研报运营身份后再发布内容。"
          variant="error"
        />
      </StandardPageLayout>
    );
  }

  return (
    <StandardPageLayout className="!max-w-[88rem]">
      <div className="flex items-center gap-2">
        <PenLine className="h-5 w-5 text-primary" />
        <h1 className="text-xl font-semibold text-foreground">研报工作台</h1>
      </div>
      <p className="text-sm text-secondary-text">
        这里用于研报运营账号撰写、发布和下架自己创建的研报。
      </p>
      <ResearchReportsStudio />
    </StandardPageLayout>
  );
};

export default ResearchReportsStudioPage;
