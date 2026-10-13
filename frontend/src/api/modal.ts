import {watch,nextTick,onUnmounted,type Ref} from 'vue'

/** Keep original-source dialogs usable without a mouse. */
export function useModal<T>(opened:Ref<T|null>){
 let previous:HTMLElement|null=null
 const key=(event:KeyboardEvent)=>{
  if(!opened.value)return
  if(event.key==='Escape'){event.preventDefault();opened.value=null;return}
  if(event.key!=='Tab')return
  const dialog=document.querySelector<HTMLElement>('[role="dialog"]')
  const controls=Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')??[])
  if(!controls.length)return
  const first=controls[0]!,last=controls[controls.length-1]!
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
 }
 document.addEventListener('keydown',key)
 watch(opened,async(value)=>{
  if(value){previous=document.activeElement instanceof HTMLElement?document.activeElement:null;await nextTick();document.querySelector<HTMLElement>('[role="dialog"] .close')?.focus()}
  else if(previous?.isConnected)previous.focus()
 })
 onUnmounted(()=>document.removeEventListener('keydown',key))
}
