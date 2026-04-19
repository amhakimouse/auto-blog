import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

console.log('[main.tsx] Script loaded');

const rootElement = document.getElementById('root');

if (!rootElement) {
  console.error('[main.tsx] Critical error: Root element with id="root" not found in document!');
} else {
  console.log('[main.tsx] Root element found, initializing ReactDOM.createRoot...');
  
  try {
    const root = ReactDOM.createRoot(rootElement);
    console.log('[main.tsx] Rendering App component...');
    root.render(
      <React.StrictMode>
        <App />
      </React.StrictMode>
    );
    console.log('[main.tsx] Render call completed successfully');
  } catch (error) {
    console.error('[main.tsx] Runtime error during initial render:', error);
  }
}
