import { useState } from 'react';
import {
  Bell,
  BellOff,
  BellRing,
  Smartphone,
  ShieldCheck,
  BatteryCharging,
  X,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
} from 'lucide-react';
import { NotificationPermissionState } from '../hooks/useBackgroundKeepAlive';

interface BackgroundSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  permission: NotificationPermissionState;
  onRequestPermission: () => Promise<NotificationPermissionState>;
  onTestNotification: () => Promise<boolean>;
  isWakeLockActive: boolean;
}

export function BackgroundSettingsModal({
  isOpen,
  onClose,
  permission,
  onRequestPermission,
  onTestNotification,
  isWakeLockActive,
}: BackgroundSettingsModalProps) {
  const [testStatus, setTestStatus] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleTest = async () => {
    setTestStatus('Sending notification...');
    const ok = await onTestNotification();
    if (ok) {
      setTestStatus('Notification sent! Check your notification tray.');
    } else {
      setTestStatus('Could not send notification. Check browser permissions.');
    }
    setTimeout(() => setTestStatus(null), 4000);
  };

  const handleEnable = async () => {
    const res = await onRequestPermission();
    if (res === 'granted') {
      await onTestNotification();
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-3 sm:p-4 animate-fadeIn">
      <div className="bg-[#1e1f29] border border-[#44475a] rounded-2xl w-full max-w-lg shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#44475a] bg-[#282a36]/50">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#bd93f9]/20 border border-[#bd93f9]/40 flex items-center justify-center text-[#bd93f9]">
              <Smartphone className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-[#f8f8f2]">Android Background Settings</h2>
              <p className="text-[11px] text-[#6272a4]">Keep EidosBot running smoothly when minimized</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a] transition active:scale-95"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-4 space-y-4 overflow-y-auto text-xs text-[#f8f8f2] flex-1">
          {/* Card 1: Notifications */}
          <div className="p-3.5 rounded-xl bg-[#282a36] border border-[#44475a] space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {permission === 'granted' ? (
                  <BellRing className="w-4 h-4 text-[#50fa7b]" />
                ) : permission === 'denied' ? (
                  <BellOff className="w-4 h-4 text-[#ff5555]" />
                ) : (
                  <Bell className="w-4 h-4 text-[#ffb86c]" />
                )}
                <span className="font-semibold">Push Notifications</span>
              </div>
              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                  permission === 'granted'
                    ? 'bg-[#50fa7b]/20 text-[#50fa7b] border border-[#50fa7b]/40'
                    : permission === 'denied'
                    ? 'bg-[#ff5555]/20 text-[#ff5555] border border-[#ff5555]/40'
                    : 'bg-[#ffb86c]/20 text-[#ffb86c] border border-[#ffb86c]/40'
                }`}
              >
                {permission === 'granted' ? 'Allowed' : permission === 'denied' ? 'Blocked' : 'Not Enabled'}
              </span>
            </div>

            <p className="text-[11px] text-[#6272a4] leading-relaxed">
              Receive phone notifications when EidosBot finishes a task, completes voice output, or requests safety approval for a sensitive action.
            </p>

            <div className="flex items-center gap-2 pt-1 flex-wrap">
              {permission !== 'granted' ? (
                <button
                  onClick={handleEnable}
                  className="px-3 py-1.5 rounded-lg bg-[#bd93f9] hover:bg-[#bd93f9]/90 text-[#1e1f29] font-bold text-xs transition active:scale-95 shadow-sm"
                >
                  Enable Notifications
                </button>
              ) : (
                <button
                  onClick={handleTest}
                  className="px-3 py-1.5 rounded-lg bg-[#44475a] hover:bg-[#6272a4] text-white font-medium text-xs transition active:scale-95"
                >
                  Send Test Notification
                </button>
              )}
            </div>

            {testStatus && (
              <div className="text-[11px] text-[#50fa7b] bg-[#50fa7b]/10 border border-[#50fa7b]/30 rounded-lg p-2 flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                <span>{testStatus}</span>
              </div>
            )}
          </div>

          {/* Card 2: Background Services Status */}
          <div className="p-3.5 rounded-xl bg-[#282a36] border border-[#44475a] space-y-2.5">
            <div className="flex items-center gap-2 font-semibold">
              <ShieldCheck className="w-4 h-4 text-[#8be9fd]" />
              <span>Background Execution Protections</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
              <div className="p-2.5 rounded-lg bg-[#1e1f29] border border-[#44475a]/60 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-[#f8f8f2]">Screen Wake Lock</span>
                  <span
                    className={`text-[10px] font-bold ${
                      isWakeLockActive ? 'text-[#50fa7b]' : 'text-[#6272a4]'
                    }`}
                  >
                    {isWakeLockActive ? 'Active' : 'Standby'}
                  </span>
                </div>
                <p className="text-[#6272a4] text-[10px]">
                  Prevents screen from sleeping while speaking or during voice playback.
                </p>
              </div>

              <div className="p-2.5 rounded-lg bg-[#1e1f29] border border-[#44475a]/60 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-[#f8f8f2]">Persistent Media Session</span>
                  <span className="text-[10px] font-bold text-[#50fa7b]">
                    Active
                  </span>
                </div>
                <p className="text-[#6272a4] text-[10px]">
                  Integrates with Android MediaSession for seamless lockscreen and background controls.
                </p>
              </div>
            </div>
          </div>

          {/* Card 3: Crucial Android Battery Optimization Instructions */}
          <div className="p-3.5 rounded-xl bg-[#ffb86c]/10 border border-[#ffb86c]/30 space-y-2">
            <div className="flex items-center gap-2 text-[#ffb86c] font-semibold">
              <BatteryCharging className="w-4 h-4" />
              <span>Important Android Battery Setting</span>
            </div>
            <p className="text-[11px] text-[#f8f8f2]/90 leading-relaxed">
              Android OS may aggressively suspend or terminate Chrome tabs when minimized to conserve power. For uninterrupted background execution:
            </p>
            <ol className="list-decimal list-inside space-y-1 text-[11px] text-[#6272a4] pl-1">
              <li>Open Android <strong className="text-[#f8f8f2]">Settings</strong> → <strong className="text-[#f8f8f2]">Apps</strong></li>
              <li>Select <strong className="text-[#f8f8f2]">Chrome</strong> (or <strong className="text-[#f8f8f2]">EidosBot</strong> if added to Home Screen)</li>
              <li>Tap <strong className="text-[#f8f8f2]">App battery usage</strong> or <strong className="text-[#f8f8f2]">Battery</strong></li>
              <li>Change from <span className="italic">Optimized</span> to <strong className="text-[#50fa7b]">Unrestricted</strong></li>
            </ol>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="p-3 border-t border-[#44475a] bg-[#282a36]/50 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-[#bd93f9] hover:bg-[#bd93f9]/90 text-[#1e1f29] font-bold text-xs transition active:scale-95"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
