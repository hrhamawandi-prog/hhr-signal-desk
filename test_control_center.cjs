const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(__dirname+'/core/control_center.html','utf8');
const elements={};
function element(){return {textContent:'',value:'',children:[],disabled:false,events:{},appendChild(c){this.children.push(c)},replaceChildren(){this.children=[]},addEventListener(k,f){this.events[k]=f}}}
const get=id=>elements[id]??=(element());
const payload={mode:'live',health:{healthy:true},controls:{pause_buys:false},csrf_token:'test',alerts:['<img onerror=boom>'],protection:'Demo only',categories:{crypto:['BTC'],stocks:['AAPL']},research:{markets:{BTC:{status:'research',regime:'range',strategies:{trend:'cash'}}}},audit:[{timestamp:'now',coin:'BTC',kind:'blocked',reason:'<img onerror=boom>'}],performance:{completed_fills:0,fees_by_currency:{},costs_note:'unknown',benchmark_note:'different exposure'}};
const requests=[];
const sandbox={document:{getElementById:get,createElement:element},Date,Intl,Number,Object,JSON,confirm:()=>true,setInterval(){},fetch:async(url,opts)=>{requests.push({url,opts});return {ok:true,json:async()=>payload}}};
vm.runInNewContext(html.split('<script>')[1].split('</script>')[0],sandbox);
setImmediate(async()=>{
 assert.equal(get('pnl').textContent,'Unavailable');
 assert.equal(get('alerts').children[0].textContent,'<img onerror=boom>');
 assert.equal(get('audit').children[0].children[3].textContent,'<img onerror=boom>');
 await get('pause').events.click();
 const request=requests.find(r=>r.url==='/api/controls');
 assert.deepEqual(JSON.parse(request.opts.body),{pause_buys:true});
 assert.equal(request.opts.headers['X-CSRF-Token'],'test');
 console.log('Control center: missing values, text-only rendering, pause request and CSRF passed.');
});
