import type { CapacitorConfig } from '@capacitor/cli';

const devUrl = process.env.CAP_DEV_URL?.trim();

const config: CapacitorConfig = {
  appId: 'cc.fiet.favilla',
  appName: 'Favilla',
  webDir: 'dist',
  plugins: {
    LocalNotifications: {
      smallIcon: 'ic_launcher',
      iconColor: '#D99477',
    },
  },
  ...(devUrl
    ? {
        server: {
          url: devUrl,
          cleartext: true,
        },
      }
    : {}),
};

export default config;
