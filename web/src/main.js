import './themes/default-dark.css';
import './themes/panic.css';
import './themes/cyberpunk.css';
import './themes/retro-95.css';
import { mount } from 'svelte';
import App from './App.svelte';

const app = mount(App, { target: document.getElementById('app') });

export default app;
