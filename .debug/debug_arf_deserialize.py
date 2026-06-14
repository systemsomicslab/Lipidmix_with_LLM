import os
import io
import test_arf

os.chdir(r'C:\Users\yuu18\Lipidmix_with_LLM')
path = r'data\AlignmentResult_2026_04_07_14_07_25_PeakProperties.arf'
with open(path, 'rb') as f:
    data = f.read()
des = test_arf.deserialize(io.BytesIO(data))
print('des len', len(des))
for i,d in enumerate(des[:10]):
    print('---', i)
    print('keys', list(d.keys()))
    ap = d.get('AlignedPeakProperties')
    print('aligned type', type(ap).__name__, 'len', len(ap) if hasattr(ap,'__len__') else None)
    if isinstance(ap, list):
        print('aligned first types', [type(x).__name__ for x in ap[:3]])
    print('sample', d.get('SampleIndex'))
