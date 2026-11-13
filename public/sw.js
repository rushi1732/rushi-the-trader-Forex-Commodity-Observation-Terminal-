// RUSHI THE TRADER - Service Worker for Mobile Notification Bar Alerts & Background Signals
const SW_VERSION = 'v1.1.0';

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Handle notification click from Mobile Notification Bar (opens or focuses terminal)
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if ('focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});

// Handle background push notifications if Web Push API is used
self.addEventListener('push', (event) => {
    if (!event.data) return;
    try {
        const payload = event.data.json();
        event.waitUntil(
            self.registration.showNotification(payload.title || 'Signal Alert', {
                body: payload.body || 'New institutional trading signal',
                icon: payload.icon || '/favicon.ico',
                badge: payload.badge || '/favicon.ico',
                vibrate: payload.vibrate || [300, 100, 300, 100, 300],
                tag: payload.tag || 'trade-signal',
                renotify: true,
                requireInteraction: true,
                data: payload.data || {}
            })
        );
    } catch (e) {
        console.warn('[SW] push event parse notice:', e);
    }
});

