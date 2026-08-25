/**
 * API Client — 资金行为雷达 V1.1
 * 封装所有后端 API 调用，统一错误处理。
 */

const API = {
  _base: '/api',

  async _fetch(path) {
    try {
      const resp = await fetch(this._base + path);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (e) {
      console.error('API error:', path, e);
      return null;
    }
  },

  async getRadar() { return this._fetch('/radar'); },
  async getStats() { return this._fetch('/stats'); },
  async getHealth() { return this._fetch('/health'); },
  async getSignals() { return this._fetch('/signals'); },
  async getTop10() { return this._fetch('/top10'); },
  async getMarketSummary() { return this._fetch('/market-summary'); },
  async getPrices() { return this._fetch('/prices'); },
  async getSymbolDetail(symbol) { return this._fetch('/symbol/' + symbol); },
};
