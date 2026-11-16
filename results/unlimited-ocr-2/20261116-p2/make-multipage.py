from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
root=Path('/work');font=ImageFont.truetype('/fonts/LiberationSans-Regular.ttf',32);bold=ImageFont.truetype('/fonts/LiberationSans-Bold.ttf',44)
for n in range(1,12):
 im=Image.new('RGB',(1240,1754),'white');draw=ImageDraw.Draw(im);draw.text((90,75),f'Service Review Page {n:02}',font=bold,fill='black')
 lines=[f'Page reference: {71000+n}','This document records service performance for an operational review.','The team measured availability, request handling, and support outcomes.','Each entry was checked against the original service activity log.','The observations are used to plan improvements for the next quarter.',f'The unique reference for this page is {71000+n}.','The summary continues on the following page.' if n<11 else 'This is the final page of the report.','End of this page.']
 if n>1:lines.insert(1,'This section continues from the previous page.')
 for i,line in enumerate(lines):draw.text((90,175+i*95),line,font=font,fill='black')
 im.save(root/f'page-{n:02}.png')
