import './theme.css';
import logo from './logo.svg';
import type { Theme } from '../types';

/**
 * KQED — PROVISIONAL. The logo and favicon were saved from kqed.org; confirm
 * them with KQED's brand team and replace with official files. See README.md.
 */
const theme: Theme = {
  id: 'kqed',
  masthead: 'band',
  // White wordmark, drawn for kqed.org's navy masthead; viewBox 99 × 31
  logo: { src: logo, alt: 'KQED', wordmark: 'KQED', width: 99, height: 31 },
};

export default theme;
