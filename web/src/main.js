import '@fontsource/atkinson-hyperlegible/400.css';
import '@fontsource/atkinson-hyperlegible/700.css';
import './styles/tokens.css';
import './styles/base.css';
import { mount } from 'svelte';
import App from './App.svelte';

export default mount(App, { target: document.getElementById('app') });
