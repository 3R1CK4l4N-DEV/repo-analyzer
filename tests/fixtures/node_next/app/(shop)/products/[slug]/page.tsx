export default function ProductPage({ params }: { params: { slug: string } }) {
  return <main>{params.slug}</main>;
}
