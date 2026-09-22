/* Stateful area-average resampling from the actual device rate to 16 kHz mono PCM16. */
class PCM16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.sum = 0; this.weight = 0; this.buffer = []; this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === 'finish') {
        this.stopped = true;
        if (this.weight) this.buffer.push(this.toInt(this.sum / this.weight));
        this.flush(); this.port.postMessage({ type: 'flushed' });
      }
    };
  }
  toInt(value) { return Math.round(Math.max(-1, Math.min(1, value)) * (value < 0 ? 32768 : 32767)); }
  flush() {
    if (!this.buffer.length) return;
    const bytes = new ArrayBuffer(this.buffer.length * 2), view = new DataView(bytes);
    this.buffer.forEach((value, i) => view.setInt16(i * 2, value, true));
    this.port.postMessage({ type: 'pcm', bytes }, [bytes]); this.buffer = [];
  }
  process(inputs) {
    if (this.stopped) return false;
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] / channels.length;
      let remaining = 1;
      while (remaining > 1e-9) {
        const amount = Math.min(remaining, this.ratio - this.weight);
        this.sum += value * amount; this.weight += amount; remaining -= amount;
        if (this.weight >= this.ratio - 1e-9) {
          this.buffer.push(this.toInt(this.sum / this.weight)); this.sum = 0; this.weight = 0;
          if (this.buffer.length === 3200) this.flush();
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm16', PCM16Processor);
