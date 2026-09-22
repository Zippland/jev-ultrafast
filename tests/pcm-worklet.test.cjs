const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('jev_ultrafast/static/pcm-worklet.js', 'utf8');
for (const rate of [16000, 44100, 48000]) {
  test(`${rate} Hz blocks become exactly 16000 PCM16 samples, with a flushed tail`, () => {
    let Processor; const messages = [];
    const context = {sampleRate:rate, AudioWorkletProcessor:class { constructor() {this.port={postMessage:m=>messages.push(m)};} },
      registerProcessor:(_,klass)=>{Processor=klass;}};
    vm.runInNewContext(source,context);
    const processor = new Processor();
    for (let offset=0; offset<rate; offset+=128) {
      const chunk = new Float32Array(Math.min(128,rate-offset)).fill(0.5);
      assert.equal(processor.process([[chunk]]),true);
    }
    processor.port.onmessage({data:'finish'});
    const pcm = messages.filter(m=>m.type==='pcm');
    assert.equal(pcm.reduce((n,m)=>n+m.bytes.byteLength,0),32000);
    for(const message of pcm) {
      const view=new DataView(message.bytes);
      for(let i=0;i<view.byteLength;i+=2) assert.equal(view.getInt16(i,true),16384);
    }
    assert.equal(messages.at(-1).type,'flushed');
    assert.equal(processor.process([[]]),false);
  });
}
