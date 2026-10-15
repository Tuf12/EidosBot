import { useState, useEffect, useRef, useCallback } from 'react';

export type NotificationPermissionState = 'default' | 'granted' | 'denied' | 'unsupported';

interface UseBackgroundKeepAliveOptions {
  isThinking?: boolean;
  isPlaying?: boolean;
  isRecording?: boolean;
}

export function useBackgroundKeepAlive({
  isThinking = false,
  isPlaying = false,
  isRecording = false,
}: UseBackgroundKeepAliveOptions = {}) {
  const [notificationPermission, setNotificationPermission] = useState<NotificationPermissionState>(() => {
    if (typeof window !== 'undefined' && 'Notification' in window) {
      return Notification.permission as NotificationPermissionState;
    }
    return 'unsupported';
  });

  const [isWakeLockActive, setIsWakeLockActive] = useState(false);
  const wakeLockRef = useRef<any>(null);

  // Sync notification permission state
  useEffect(() => {
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    setNotificationPermission(Notification.permission as NotificationPermissionState);

    // Also check Permissions API if supported
    if ('permissions' in navigator && navigator.permissions.query) {
      navigator.permissions
        .query({ name: 'notifications' as PermissionName })
        .then((status) => {
          setNotificationPermission(status.state as NotificationPermissionState);
          status.onchange = () => {
            setNotificationPermission(status.state as NotificationPermissionState);
          };
        })
        .catch(() => {});
    }
  }, []);

  const requestNotificationPermission = useCallback(async (): Promise<NotificationPermissionState> => {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      setNotificationPermission('unsupported');
      return 'unsupported';
    }
    try {
      const result = await Notification.requestPermission();
      const state = result as NotificationPermissionState;
      setNotificationPermission(state);
      return state;
    } catch (e) {
      console.debug('[Notifications] requestPermission error:', e);
      return 'denied';
    }
  }, []);

  const sendNotification = useCallback(
    async (title: string, options?: NotificationOptions): Promise<boolean> => {
      if (typeof window === 'undefined' || !('Notification' in window)) return false;
      if (Notification.permission !== 'granted') return false;

      const opts: NotificationOptions = {
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-192.png',
        tag: 'eidos-background-event',
        data: { url: '/' },
        // Vibration pattern for Android: vibrate 200ms, pause 100ms, vibrate 200ms
        ...((options as any) || {}),
      };
      if (!(opts as any).vibrate) {
        (opts as any).vibrate = [200, 100, 200];
      }

      // Method 1: Service Worker registration.showNotification (recommended on Android Chrome)
      if ('serviceWorker' in navigator) {
        try {
          const reg = await navigator.serviceWorker.ready;
          if (reg && 'showNotification' in reg) {
            await reg.showNotification(title, opts);
            return true;
          }
        } catch (e) {
          console.debug('[Notifications] SW showNotification error:', e);
        }

        // Method 2: Post message to active SW controller
        try {
          if (navigator.serviceWorker.controller) {
            navigator.serviceWorker.controller.postMessage({
              type: 'SHOW_NOTIFICATION',
              title,
              options: opts,
            });
            return true;
          }
        } catch (e) {
          console.debug('[Notifications] SW postMessage error:', e);
        }
      }

      // Method 3: Standard window Notification constructor fallback
      try {
        new Notification(title, opts);
        return true;
      } catch (e) {
        console.debug('[Notifications] window.Notification error:', e);
        return false;
      }
    },
    []
  );

  const testNotification = useCallback(async () => {
    let perm = notificationPermission;
    if (perm !== 'granted') {
      perm = await requestNotificationPermission();
    }
    if (perm === 'granted') {
      await sendNotification('EidosBot Notifications Active', {
        body: 'Background alerts are ready! You will be notified when tasks finish or need approval.',
        tag: 'eidos-test-notification',
      });
      return true;
    }
    return false;
  }, [notificationPermission, requestNotificationPermission, sendNotification]);

  // Screen Wake Lock: Keep phone screen on while listening (STT), while EidosBot is computing, or during speech playback
  useEffect(() => {
    const shouldWakeLock = isRecording || isThinking || isPlaying;

    async function acquireLock() {
      if (typeof navigator === 'undefined' || !('wakeLock' in navigator)) return;
      if (wakeLockRef.current) return;
      try {
        const lock = await (navigator as any).wakeLock.request('screen');
        wakeLockRef.current = lock;
        setIsWakeLockActive(true);
        lock.addEventListener('release', () => {
          wakeLockRef.current = null;
          setIsWakeLockActive(false);
        });
      } catch (err) {
        console.debug('[WakeLock] Acquire error:', err);
      }
    }

    function releaseLock() {
      if (wakeLockRef.current) {
        try {
          wakeLockRef.current.release().catch(() => {});
        } catch {}
        wakeLockRef.current = null;
        setIsWakeLockActive(false);
      }
    }

    if (shouldWakeLock) {
      acquireLock();
    } else {
      releaseLock();
    }

    // Re-acquire lock if tab becomes visible again while work or speech is still in motion
    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && (isRecording || isThinking || isPlaying)) {
        acquireLock();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      releaseLock();
    };
  }, [isRecording, isThinking, isPlaying]);

  return {
    notificationPermission,
    requestNotificationPermission,
    sendNotification,
    testNotification,
    isWakeLockActive,
  };
}
