const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium'}).catch(()=>chromium.launch());
const p=await b.newPage({viewport:{width:1920,height:1080}});
await p.goto('file://'+__dirname+'/intro.html');await p.evaluate(()=>document.fonts.ready);
await p.evaluate(()=>Promise.all([document.fonts.load('190px Anton'),document.fonts.load('800 64px Inter')]));
for(const w of ['five','respect']){require('fs').mkdirSync(w,{recursive:true});
 for(let i=0;i<60;i++){await p.evaluate(([w,t])=>draw(w,t),[w,i/30]);await p.locator('#c').screenshot({path:`${w}/f${String(i).padStart(3,'0')}.png`});}}
await b.close();})();
