import {chromium} from 'playwright';import {readFileSync} from 'fs';
import {fileURLToPath} from 'url';import {dirname,resolve} from 'path';
const H=dirname(fileURLToPath(import.meta.url));
const draft=JSON.parse(readFileSync(resolve(H,'fixture-draft.json'),'utf8'));
const traded=JSON.parse(readFileSync(resolve(H,'fixture-traded.json'),'utf8'));
const users=[[1,'NY Kat Snatchers'],[2,'East Coast Wins Most'],[3,'Manhattan Meatpacker'],[4,"It's Gonna Be Maye"],
 [5,'Jersey Rum Hams'],[6,'Hulleywood'],[7,'Scared Hitless'],[8,'Randiculous'],[9,'Made America 2024'],
 [10,'Down to Pound'],[11,'Mountain Hermits'],[12,'Travis Swift']];
const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium'});
const pg=await b.newPage({viewport:{width:1400,height:1180}});
await pg.addInitScript(({draft,traded,users})=>{
  window.__picks=[{pick_no:98,roster_id:1,player_id:'x1',is_keeper:true,metadata:{first_name:'Cam',last_name:'Skattebo'}},
                  {pick_no:146,roster_id:1,player_id:'x2',is_keeper:true,metadata:{first_name:'Rico',last_name:'Dowdle'}}];
  const json=d=>Promise.resolve({ok:true,status:200,headers:{get:()=>'W/"'+JSON.stringify(d).length+'"'},json:()=>Promise.resolve(d)});
  window.fetch=u=>{u=String(u);
    if(u.includes('/picks'))return json(window.__picks);
    if(u.includes('/traded_picks'))return json(traded);
    if(/\/draft\/\d+\?/.test(u))return json(draft);
    if(u.includes('/drafts'))return json([draft]);
    if(u.includes('/users'))return json(users.map(([r,n])=>({user_id:'u'+r,display_name:n,metadata:{team_name:n}})));
    if(u.includes('/rosters'))return json(users.map(([r])=>({roster_id:r,owner_id:'u'+r})));
    return json([])};
},{draft,traded,users});
await pg.goto('file://'+resolve(H,'..','dist','draft-live.html'));
await pg.evaluate(()=>localStorage.clear()); await pg.reload();
await pg.waitForFunction(()=>typeof SYNC!=='undefined'&&SYNC.on,null,{timeout:5000});
// play out 22 picks so the board has life in it
await pg.evaluate(()=>{const used=new Set(window.__picks.map(p=>p.pick_no));
  const ids=Object.keys(D.idToName);
  for(let n=1;n<=22;n++){if(used.has(n))continue;
    window.__picks.push({pick_no:n,roster_id:((n-1)%12)+1,player_id:ids[n*3%ids.length],metadata:{}})}
  return pollOnce()});
await pg.waitForTimeout(400);
await pg.emulateMedia({colorScheme:'dark'}); await pg.waitForTimeout(200);
await pg.screenshot({path:'/tmp/app-shot.png'});
await b.close(); console.log('shot saved');
