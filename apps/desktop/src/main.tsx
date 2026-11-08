import React from 'react';
import ReactDOM from 'react-dom/client';
// V2 typography: rounded neo-grotesque stack. 'Arial Rounded MT Bold' is used when installed
// (ships with Windows Office); Nunito is the bundled rounded fallback, Inter the last resort.
import '@fontsource/nunito/500.css';
import '@fontsource/nunito/600.css';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/800.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
// Art Update brand type (all OFL, bundled offline): Unbounded for the wordmark, clock and headings, Figtree for
// everything you read, Martian Mono for code and numbers. Nunito above stays as the fallback.
import '@fontsource-variable/unbounded';
import '@fontsource-variable/figtree';
import '@fontsource-variable/martian-mono/standard.css';
import './index.css';
import App from './App';
import { DEMO, installDemoFetch } from './demo/demoDaemon';
import { installDaemonAuth } from './lib/daemonAuth';
import DemoBanner from './demo/DemoBanner';
import Tour from './demo/Tour';
import GuideOverlayRoot, { isGuideOverlayHash } from './components/shell/GuideOverlayRoot'; // [v1gaps] Windows guide ring windows

if (DEMO) installDemoFetch();
else installDaemonAuth(); // review B1: every daemon call carries its per-start key

const overlay = isGuideOverlayHash(window.location.hash); // [v1gaps]
ReactDOM.createRoot(document.getElementById('root')!).render(
  overlay ? <GuideOverlayRoot which={window.location.hash} /> : <React.StrictMode>
    <App />
    {DEMO && <DemoBanner />}
    {DEMO && <Tour />}
  </React.StrictMode>,
);
